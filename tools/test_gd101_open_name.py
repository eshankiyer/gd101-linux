import unittest
from unittest.mock import Mock
from gd101_abi import LifecycleProvider, install
from test_gd101_abi_deadline import Exports


class OpenNameTests(unittest.TestCase):
    def test_default_patterns_and_rejected_suffixes(self):
        ffi = Exports()
        provider = LifecycleProvider()
        provider.open = Mock(return_value=(0, 42))
        install(ffi, provider)
        output = ffi.new('uint32_t *')
        for name in (None, b'J2534-1:', b'J2534-2:'):
            value = ffi.NULL if name is None else ffi.new('char[]', name)
            self.assertEqual(ffi.functions['PassThruOpen'](value, output), 0)
            self.assertEqual(output[0], 42)
        self.assertEqual(provider.open.call_count, 3)
        for name in (b'', b'J', b'J2534-2:other', b'J2534-3:', b'J2534-2', b'J2534-2;'):
            output[0] = 99
            self.assertEqual(ffi.functions['PassThruOpen'](ffi.new('char[]', name), output), 1)
            self.assertEqual(output[0], 0)
        self.assertEqual(provider.open.call_count, 3)


if __name__ == '__main__':
    unittest.main()
