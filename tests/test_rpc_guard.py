import threading
import unittest
from decimal import Decimal
from unittest.mock import patch

from web3 import Web3
from core import Engine, Limits, ReadOnlyProvider, UserError, number


class GuardTests(unittest.TestCase):
    def test_write_methods_blocked_before_any_network(self):
        provider = ReadOnlyProvider('https://example.invalid')
        for method in ('eth_sendRawTransaction', 'eth_sendTransaction', 'eth_sign',
                       'personal_sign', 'eth_signTypedData_v4', 'anvil_setBalance'):
            with self.subTest(method=method), patch.object(Web3.HTTPProvider, 'make_request') as network:
                with self.assertRaises(UserError):
                    provider.make_request(method, [])
                network.assert_not_called()

    def test_mixed_batches_blocked_before_network(self):
        provider = ReadOnlyProvider('https://example.invalid')
        with patch.object(Web3.HTTPProvider, 'make_batch_request') as network:
            with self.assertRaises(UserError):
                provider.make_batch_request([('eth_chainId', []), ('eth_sendRawTransaction', ['synthetic'])])
            network.assert_not_called()

    def test_chain_reads_allowed(self):
        provider = ReadOnlyProvider('https://example.invalid')
        with patch.object(Web3.HTTPProvider, 'make_request', return_value={'result': '0x38'}) as network:
            self.assertEqual(provider.make_request('eth_chainId', [])['result'], '0x38')
            network.assert_called_once()

    def test_no_execution_helpers(self):
        engine = Engine('https://example.invalid', Limits(), threading.Event())
        for method in ('swap', 'transfer', '_send', 'approve', 'sign'):
            self.assertFalse(hasattr(engine, method))

    def test_rpc_and_slippage_validation(self):
        for rpc in ('http://example.com', 'https://user:password@example.com', 'ftp://example.com'):
            with self.assertRaises(UserError):
                Engine(rpc, Limits(), threading.Event())
        for slip in ('0', '21', 'NaN'):
            with self.assertRaises(UserError):
                Engine('https://example.invalid', Limits(Decimal(slip)), threading.Event())

    def test_invalid_numbers(self):
        for value in ('NaN', 'Infinity', '-1', '1e99', 'invalid'):
            with self.assertRaises(UserError):
                number(value)


if __name__ == '__main__':
    unittest.main(verbosity=2)
