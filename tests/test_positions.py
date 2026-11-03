"""Position valuation tests: mock reads; no signing or broadcast endpoints."""
import json
import tempfile
import time
import unittest
from decimal import Decimal
from pathlib import Path
from unittest.mock import Mock, patch

import secrets
from web3.exceptions import ContractLogicError
from core import UserError, Limits, address
from positions import PositionBook, SETTLEMENTS, validate_position, valuation_math, estimate_exit


def position():
    return {'id': 'a' * 32, 'name': '真实持仓', 'currency': 'USDT',
            'token': address('0x' + secrets.token_hex(20)), 'wallets': [address('0x' + secrets.token_hex(20)), address('0x' + secrets.token_hex(20))],
            'cost': '100', 'exit_fee': '2', 'target_pct': '10', 'transfer_loss_pct': '1'}


class BookTests(unittest.TestCase):
    def test_public_data_only_and_roundtrip(self):
        with tempfile.TemporaryDirectory() as temp:
            book = PositionBook(Path(temp) / 'positions.json')
            row = position()
            row['key'] = 'THIS_MUST_NOT_BE_SAVED'
            saved = book.add(row)
            self.assertNotIn('key', saved)
            self.assertNotIn('THIS_MUST_NOT_BE_SAVED', book.path.read_text('utf-8'))
            self.assertEqual([saved], book.read())

    def test_validation(self):
        for field, invalid in [('cost', '0'), ('cost', 'NaN'), ('exit_fee', '-1'),
                               ('target_pct', '0'), ('transfer_loss_pct', '100'),
                               ('currency', 'XYZ'), ('wallets', []), ('name', '')]:
            with self.subTest(field=field):
                row = position()
                row[field] = invalid
                with self.assertRaises(UserError):
                    validate_position(row)

    def test_duplicate_wallets_do_not_double_count(self):
        row = position()
        row['wallets'] = row['wallets'] * 3
        self.assertEqual(2, len(validate_position(row)['wallets']))

    def test_same_target_and_currency_rejected(self):
        row = position()
        row['token'] = SETTLEMENTS['USDT']
        with self.assertRaises(UserError):
            validate_position(row)

    def test_failed_write_preserves_book(self):
        with tempfile.TemporaryDirectory() as temp:
            book = PositionBook(Path(temp) / 'positions.json')
            book.add(position())
            original = book.path.read_bytes()
            with patch('positions.os.replace', side_effect=OSError('disk full')), self.assertRaises(OSError):
                book.add(position())
            self.assertEqual(original, book.path.read_bytes())

    def test_corrupt_book_is_not_overwritten(self):
        with tempfile.TemporaryDirectory() as temp:
            book = PositionBook(Path(temp) / 'positions.json')
            book.path.write_text('broken')
            with self.assertRaises(UserError):
                book.add(position())
            self.assertEqual('broken', book.path.read_text())


class ValuationTests(unittest.TestCase):
    def test_profit_after_slippage_and_fee(self):
        result = valuation_math(2 * 10**18, 18, 120 * 10**6, 6, position(), Decimal('1'))
        self.assertEqual(Decimal('116.8'), result['net'])
        self.assertEqual(Decimal('16.8'), result['pct'])
        self.assertTrue(result['reached'])

    def test_loss_and_zero_holdings(self):
        result = valuation_math(0, 18, 0, 6, position(), Decimal('1'))
        self.assertEqual(Decimal('-2'), result['net'])
        self.assertFalse(result['reached'])

    def engine(self, row):
        engine = Mock()
        engine.limits = Limits()
        engine.wbnb = address('0x' + secrets.token_hex(20))
        engine.w3.eth.get_block.return_value = {'number': 123, 'timestamp': time.time()}
        token, stable = Mock(), Mock()
        token.address, stable.address = row['token'], SETTLEMENTS[row['currency']]
        engine.token.side_effect = lambda a: token if a == row['token'] else stable
        engine.decimals.side_effect = lambda t: 18 if t is token else 6
        token.functions.balanceOf.return_value.call.return_value = 10**18
        return engine, token

    def test_aggregate_balances_same_block_and_best_route(self):
        row = position()
        engine, token = self.engine(row)
        def quote(raw, path):
            self.assertEqual(198 * 10**16, raw)
            call = Mock()
            call.call.return_value = [raw, (120 if len(path) == 3 else 110) * 10**6]
            return call
        engine.router.functions.getAmountsOut.side_effect = quote
        result = estimate_exit(engine, row)
        self.assertEqual(Decimal(2), result['holdings'])
        self.assertEqual(Decimal('1.98'), result['effective'])
        self.assertEqual(3, len(result['path']))
        self.assertEqual(Decimal(120), result['gross'])
        for call in token.functions.balanceOf.return_value.call.call_args_list:
            self.assertEqual({'block_identifier': 123}, call.kwargs)
        engine.swap.assert_not_called()
        engine.transfer.assert_not_called()
        engine._send.assert_not_called()

    def test_unavailable_direct_route_uses_wbnb_route(self):
        row = position()
        engine, _ = self.engine(row)
        engine.router.functions.getAmountsOut.return_value.call.side_effect = [ContractLogicError('execution reverted'), [1, 110 * 10**6]]
        self.assertEqual(3, len(estimate_exit(engine, row)['path']))

    def test_connection_failure_does_not_fabricate_profit(self):
        row = position()
        engine, _ = self.engine(row)
        engine.router.functions.getAmountsOut.return_value.call.side_effect = ConnectionError('synthetic outage')
        with self.assertRaises(ConnectionError):
            estimate_exit(engine, row)

    def test_stale_block_stops(self):
        row = position()
        engine, _ = self.engine(row)
        engine.w3.eth.get_block.return_value['timestamp'] = time.time() - 120
        with self.assertRaises(UserError):
            estimate_exit(engine, row)

    def test_zero_balance_does_not_request_quote(self):
        row = position()
        engine, token = self.engine(row)
        token.functions.balanceOf.return_value.call.return_value = 0
        result = estimate_exit(engine, row)
        self.assertFalse(result['reached'])
        engine.router.functions.getAmountsOut.assert_not_called()



if __name__ == '__main__':
    unittest.main(verbosity=2)
