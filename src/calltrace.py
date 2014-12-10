# The MIT License (MIT)
#
# Copyright (c) 2014 Rob van der Most
#
# Permission is hereby granted, free of charge, to any person obtaining a copy
# of this software and associated documentation files (the "Software"), to deal
# in the Software without restriction, including without limitation the rights
# to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
# copies of the Software, and to permit persons to whom the Software is
# furnished to do so, subject to the following conditions:
#
# The above copyright notice and this permission notice shall be included in all
# copies or substantial portions of the Software.
#
# THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
# IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
# FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
# AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
# LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
# OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
# SOFTWARE.
"""
Call tracing for single functions and methods or complete classes. The only public interface
is the @trace decorator.
"""
from __future__ import print_function

__author__ = 'Silvester747@gmail.com'

import inspect
import traceback
import unittest

import logging
log = logging.getLogger('CallTracing')

# TODO: Show defaults too
# TODO: Handle properties (maybe use class_attrs for everything?)


def trace(obj):
    """
    Trace all calls to a function, method or all methods in a class. Simply annotate any of them
    with @trace. Uses Python logging at DEBUG level with name 'CallTracing'.

    Tries do prepare as much as possible at the time of class definition and limit the cycles wasted
    during each traced call.
    """
    if inspect.isclass(obj):
        return _trace_class(obj)
    elif inspect.ismethod(obj) or inspect.isfunction(obj):
        return _trace_method(obj)
    else:
        raise TypeError('Cannot trace this object.')


def _trace_class(c):
    class_attrs = inspect.classify_class_attrs(c)
    methods = inspect.getmembers(c, lambda obj: inspect.ismethod(obj) or inspect.isfunction(obj))
    for name, method in methods:

        if hasattr(method, 'tracer'):
            # Method is already being traced
            continue

        for attr_name, attr_kind, _, _ in class_attrs:
            if attr_name == name:
                kind = attr_kind
                break
        else:
            continue

        if kind == 'method':
            decorator = lambda f: f
        elif kind == 'static method':
            decorator = staticmethod
        elif kind == 'class method':
            decorator = classmethod
            # To relay calls to the class method, we need to use the underlying function
            method = method.__func__
        else:
            continue

        setattr(c, name, decorator(_trace_method(method, c.__name__)))

    return c


def _trace_method(func, class_name=None):
    assert inspect.ismethod(func) or inspect.isfunction(func)
    return _FunctionTracer(func, class_name).wrapped_function()


def _safe_str(obj):
    """
    Get string representations for objects without causing recursions in traced objects. Goes
    together with _check_recursion_loop().
    """
    try:
        return str(obj)
    except RuntimeError:
        # Infinite recursion should no longer happen, but let's be safe
        return str(id(obj))


# Stack traces contain .py files, but __file__ can be .pyc too. Make sure the
# right file is expected
if __file__.endswith('.pyc'):
    _my_source_file = __file__[:-1]
else:
    _my_source_file = __file__


def _check_recursion_loop():
    """
    Check that we are causing an infinite loop. This happens when str() uses __str__() which
    calls another traced method.

    This only works if this module consistently uses _safe_str() instead of str()

    :return: True if we are looping.
    """
    stack = traceback.extract_stack()
    me = [name
          for filename, _, name, _
          in stack
          if name == _safe_str.__name__ and filename == _my_source_file]
    return len(me) > 0


class _FunctionTracer(object):
    def __init__(self, func, class_name=None):
        self._func = func
        self._get_details()

        # Static/class methods do not have the class associated, so allow overriding after trying
        # to retrieve from function object
        if class_name and not self._class_name:
            self._class_name = class_name

        self._select_call_format()
        self._select_handle_self()

    def _get_details(self):
        self._name = self._func.__name__

        self._class_name = None
        if inspect.ismethod(self._func):
            if self._func.im_class:
                self._class_name = self._func.im_class.__name__
            real_func = self._func.__func__
        else:
            real_func = self._func

        self._arg_names = inspect.getargspec(real_func).args

        self._is_init = self._name == '__init__'
        self._has_self = 'self' in self._arg_names

    def _select_call_format(self):
        if self._class_name and self._has_self:
            self._name_format = '{class_name}[{instance_id}].{func_name}'
        elif self._class_name and not self._has_self:
            self._name_format = '{class_name}.{func_name}'
        elif not self._class_name and self._has_self:
            self._name_format = '[{instance_id}].{func_name}'
        else:
            self._name_format = '{func_name}'

        self._name_format = self._name_format.format(class_name=self._class_name,
                                                     func_name=self._name,
                                                     instance_id='{instance_id}')

        self._call_format = self._name_format + '({pargs}{comma}{kwargs})'
        self._return_format = self._name_format + ' returned {return_value}'
        self._exception_format = self._name_format + ' raised an exception'

    def _select_handle_self(self):
        if self._has_self and self._is_init:
            # Need to ignore self
            self._arg_names = self._arg_names[1:]
            self._handle_self = self._handle_self_remove_self
        elif self._has_self:
            self._handle_self = self._handle_self_use_self
        else:
            self._handle_self = self._handle_self_no_self

    @staticmethod
    def _handle_self_remove_self(pargs, kwargs):
        if 'self' in kwargs:
            instance = id(kwargs['self'])
            kwargs = {k: v for k, v in kwargs.items() if k != 'self'}
        elif len(pargs) > 0:
            instance = id(pargs[0])
            pargs = pargs[1:]
        else:
            instance = None
        return pargs, kwargs, instance

    @staticmethod
    def _handle_self_use_self(pargs, kwargs):
        if 'self' in kwargs:
            instance = id(kwargs['self'])
        elif len(pargs) > 0:
            instance = id(pargs[0])
        else:
            instance = None
        return pargs, kwargs, instance

    @staticmethod
    def _handle_self_no_self(pargs, kwargs):
        return pargs, kwargs, None

    def wrapped_function(self):
        """
        Return a function and not a (un)bound method. We do not want to interfere with self.
        """
        def wrapped(*pargs, **kwargs):
            return wrapped.tracer._call(pargs, kwargs)
        wrapped.tracer = self
        wrapped.__name__ = self._func.__name__
        wrapped.__doc__ = self._func.__doc__
        return wrapped

    def _call(self, pargs, kwargs):
        looping = _check_recursion_loop()
        instance = 0  # in case exception is raised in a loop
        if not looping:
            filtered_pargs, filtered_kwargs, instance = self._handle_self(pargs, kwargs)
            self._log_call(filtered_pargs, filtered_kwargs, instance)
        try:
            ret = self._func(*pargs, **kwargs)
            if not looping:
                self._log_return(ret, instance)
            return ret
        except:
            self._log_exception(instance)
            raise

    def _log_call(self, pargs, kwargs, instance):
        log.debug(self._format_call(pargs, kwargs, instance))

    def _format_call(self, pargs, kwargs, instance):
        formatted_pargs = self._format_arguments(zip(self._arg_names, pargs))
        formatted_kwargs = self._format_arguments(kwargs.items())
        comma = ', ' if formatted_pargs and formatted_kwargs else ''

        return self._call_format.format(instance_id=instance,
                                        pargs=formatted_pargs,
                                        kwargs=formatted_kwargs,
                                        comma=comma)

    @staticmethod
    def _format_arguments(arg_pairs):
        if arg_pairs:
            return ', '.join('{}={}'.format(key, _safe_str(value)) for key, value in arg_pairs)
        else:
            return ''

    def _log_return(self, return_value, instance):
        log.debug(self._return_format.format(instance_id=instance,
                                             return_value=return_value))

    def _log_exception(self, instance):
        log.exception(self._exception_format.format(instance_id=instance))


class _TestCallTraceDecorator(unittest.TestCase):
    def __init__(self, methodName='runTest'):
        super(_TestCallTraceDecorator, self).__init__(methodName)

        # Prevent requiring mock for normal use
        import mock
        self._mock = mock

        self._original_log = None

        self.maxDiff = None

    def setUp(self):
        global log
        self._original_log = log
        log = self._mock.MagicMock()

    def tearDown(self):
        global log
        log = self._original_log

    def test_function_decorating(self):
        @trace
        def _test_func(a, b, c, d=34):
            return a+b+c+d

        ret = _test_func(1, 2, c=3)
        self.assertEqual(ret, 40)

        expected = [self._mock.call.debug('_test_func(a=1, b=2, c=3)'),
                    self._mock.call.debug('_test_func returned 40')]
        self.assertListEqual(log.mock_calls, expected)

    def test_method_decorating_old_style_class(self):
        class _TestClass:
            @trace
            def test_method(self, a, b, c, d=34):
                return a+b+c+d

        tc = _TestClass()
        ret = tc.test_method(1, 2, c=3)
        self.assertEqual(ret, 40)

        expected = [self._mock.call.debug('[{}].test_method(self={}, a=1, b=2, c=3)'.format(id(tc), str(tc))),
                    self._mock.call.debug('[{}].test_method returned 40'.format(id(tc)))]
        self.assertListEqual(log.mock_calls, expected)

    def test_method_decorating_new_style_class(self):
        class _TestClass(object):
            @trace
            def test_method(self, a, b, c, d=34):
                return a+b+c+d

        tc = _TestClass()
        ret = tc.test_method(1, 2, c=3)
        self.assertEqual(ret, 40)

        expected = [self._mock.call.debug('[{}].test_method(self={}, a=1, b=2, c=3)'.format(id(tc), str(tc))),
                    self._mock.call.debug('[{}].test_method returned 40'.format(id(tc)))]
        self.assertListEqual(log.mock_calls, expected)

    def test_class_decorating_old_style(self):
        @trace
        class _TestClass:
            def test_method(self, a, b, c, d=34):
                return a+b+c+d

            def test_method2(self, e, f):
                return e+f

        tc = _TestClass()

        ret = tc.test_method(2, 3, 4)
        self.assertEqual(ret, 43)

        ret = tc.test_method2(5, 6)
        self.assertEqual(ret, 11)

        expected = [self._mock.call.debug('_TestClass[{}].test_method(self={}, a=2, b=3, c=4)'.format(id(tc), str(tc))),
                    self._mock.call.debug('_TestClass[{}].test_method returned 43'.format(id(tc))),
                    self._mock.call.debug('_TestClass[{}].test_method2(self={}, e=5, f=6)'.format(id(tc), str(tc))),
                    self._mock.call.debug('_TestClass[{}].test_method2 returned 11'.format(id(tc)))]
        self.assertListEqual(log.mock_calls, expected)

    def test_class_decorating_new_style(self):
        @trace
        class _TestClass(object):
            def test_method(self, a, b, c, d=34):
                return a+b+c+d

            def test_method2(self, e, f):
                return e+f

        tc = _TestClass()

        ret = tc.test_method(2, 3, 4)
        self.assertEqual(ret, 43)

        ret = tc.test_method2(5, 6)
        self.assertEqual(ret, 11)

        expected = [self._mock.call.debug('_TestClass[{}].test_method(self={}, a=2, b=3, c=4)'.format(id(tc), str(tc))),
                    self._mock.call.debug('_TestClass[{}].test_method returned 43'.format(id(tc))),
                    self._mock.call.debug('_TestClass[{}].test_method2(self={}, e=5, f=6)'.format(id(tc), str(tc))),
                    self._mock.call.debug('_TestClass[{}].test_method2 returned 11'.format(id(tc)))]
        self.assertListEqual(log.mock_calls, expected)

    def test_prevent_recursion_str(self):
        """
        If __str__ is present and traced or calls a traced method, a loop
        can occur. This should be prevented.
        """
        @trace
        class _TestClass(object):
            def method(self):
                return 'Return'

            def my_name(self):
                return 'My Name'

            def __str__(self):
                return self.my_name()

        tc = _TestClass()

        self.assertEqual(str(tc), 'My Name')

        ret = tc.method()
        self.assertEqual(ret, 'Return')

        expected = [self._mock.call.debug('_TestClass[{}].__str__(self=My Name)'.format(id(tc))),
                    self._mock.call.debug('_TestClass[{}].my_name(self=My Name)'.format(id(tc))),
                    self._mock.call.debug('_TestClass[{}].my_name returned My Name'.format(id(tc))),
                    self._mock.call.debug('_TestClass[{}].__str__ returned My Name'.format(id(tc))),
                    # Here __str__ and calls from it are suppressed
                    self._mock.call.debug('_TestClass[{}].method(self=My Name)'.format(id(tc))),
                    self._mock.call.debug('_TestClass[{}].method returned Return'.format(id(tc)))]
        self.assertListEqual(log.mock_calls, expected)

    def test_init_prevent_access_to_members(self):
        """
        When tracing __init__ we must prevent calling __str__ as it can try to access the instance
        before it is ready to use.
        """
        @trace
        class _TestClass(object):
            def __init__(self, a):
                self._a = a

            def __str__(self):
                return self._a

        tc = _TestClass('bla')
        expected = [self._mock.call.debug('_TestClass[{}].__init__(a=bla)'.format(id(tc))),
                    self._mock.call.debug('_TestClass[{}].__init__ returned None'.format(id(tc)))]
        self.assertListEqual(log.mock_calls, expected)

    def test_method_raises_exception(self):
        @trace
        class _TestClass(object):
            def boom(self):
                raise TypeError('badaboom')

        tc = _TestClass()
        with self.assertRaises(TypeError):
            tc.boom()

        expected = [self._mock.call.debug('_TestClass[{}].boom(self={})'.format(id(tc), str(tc))),
                    self._mock.call.exception('_TestClass[{}].boom raised an exception'.format(id(tc)))]
        self.assertListEqual(log.mock_calls, expected)

    def test_masquerade(self):
        @trace
        class _TestClass(object):
            def test_method(self, a, b, c, d=34):
                """First test method"""
                return a+b+c+d

            def test_method2(self, e, f):
                """Second test method"""
                return e+f

        tc = _TestClass()

        self.assertEqual(tc.test_method.__name__, 'test_method')
        self.assertEqual(tc.test_method.__doc__, 'First test method')

        self.assertEqual(tc.test_method2.__name__, 'test_method2')
        self.assertEqual(tc.test_method2.__doc__, 'Second test method')

    def test_static_method(self):
        @trace
        class _TestClass(object):
            @staticmethod
            def test_method(a, b, c, d=34):
                return a+b+c+d

        ret = _TestClass.test_method(1, 2, c=3)
        self.assertEqual(ret, 40)

        expected = [self._mock.call.debug('_TestClass.test_method(a=1, b=2, c=3)'),
                    self._mock.call.debug('_TestClass.test_method returned 40')]
        self.assertListEqual(log.mock_calls, expected)

    def test_class_method(self):
        @trace
        class _TestClass(object):
            @classmethod
            def test_method(cls, a, b, c, d=34):
                return a+b+c+d

        ret = _TestClass.test_method(1, 2, c=3)
        self.assertEqual(ret, 40)

        expected = [self._mock.call.debug('_TestClass.test_method(cls={}, a=1, b=2, c=3)'.format(str(_TestClass))),
                    self._mock.call.debug('_TestClass.test_method returned 40')]
        self.assertListEqual(log.mock_calls, expected)

    def test_sub_class(self):
        @trace
        class _BaseClass(object):
            def method_one(self, a, b):
                return a+b

            def method_two(self, c, d):
                return c+d

        @trace
        class _SubClass(_BaseClass):
            def method_two(self, c, d):
                return c-d

            def method_three(self, e, f):
                return e+f

        bc = _BaseClass()
        sc = _SubClass()

        ret = bc.method_one(1, 2)
        self.assertEqual(ret, 3)

        ret = bc.method_two(4, 5)
        self.assertEqual(ret, 9)

        ret = sc.method_one(1, 2)
        self.assertEqual(ret, 3)

        ret = sc.method_two(4, 5)
        self.assertEqual(ret, -1)

        ret = sc.method_three(3, 1)
        self.assertEqual(ret, 4)

        expected = [self._mock.call.debug('_BaseClass[{}].method_one(self={}, a=1, b=2)'.format(id(bc), str(bc))),
                    self._mock.call.debug('_BaseClass[{}].method_one returned 3'.format(id(bc))),
                    self._mock.call.debug('_BaseClass[{}].method_two(self={}, c=4, d=5)'.format(id(bc), str(bc))),
                    self._mock.call.debug('_BaseClass[{}].method_two returned 9'.format(id(bc))),
                    self._mock.call.debug('_BaseClass[{}].method_one(self={}, a=1, b=2)'.format(id(sc), str(sc))),
                    self._mock.call.debug('_BaseClass[{}].method_one returned 3'.format(id(sc))),
                    self._mock.call.debug('_SubClass[{}].method_two(self={}, c=4, d=5)'.format(id(sc), str(sc))),
                    self._mock.call.debug('_SubClass[{}].method_two returned -1'.format(id(sc))),
                    self._mock.call.debug('_SubClass[{}].method_three(self={}, e=3, f=1)'.format(id(sc), str(sc))),
                    self._mock.call.debug('_SubClass[{}].method_three returned 4'.format(id(sc)))]
        self.assertListEqual(log.mock_calls, expected)

