"""Public position bookkeeping and READ-ONLY exit valuations.

This module never signs, approves, transfers, or submits a transaction.
"""
import json
import os
import time
import uuid
from decimal import Decimal
from pathlib import Path

from web3.exceptions import ContractLogicError, Web3RPCError

from core import UserError, address, number

SETTLEMENTS = {
    'BNB': None,
    'USDT': address('0x55d398326f99059fF775485246999027B3197955'),
    'USDC': address('0x8AC76a51cc950d9822D68b83fE1Ad97B32Cd580d'),
}


def validate_position(row):
    if row['currency'] not in SETTLEMENTS:
        raise UserError('结算币种仅支持 BNB、USDT、USDC')
    token = address(row['token'])
    if token == SETTLEMENTS[row['currency']]:
        raise UserError('持仓代币与结算代币不能相同')
    wallets = list(dict.fromkeys(address(a) for a in row['wallets']))
    if not wallets or len(wallets) > 200:
        raise UserError('持仓地址数量为 1–200')
    number(row['cost'], positive=True)
    number(row['exit_fee'])
    number(row['target_pct'], positive=True)
    loss = number(row['transfer_loss_pct'])
    if loss >= 100:
        raise UserError('归集数量损耗预留必须小于 100%')
    name = str(row['name']).strip()
    if not name or len(name) > 60:
        raise UserError('持仓名称为 1–60 字')
    return {'id': str(row.get('id', '')), 'name': name, 'token': token, 'wallets': wallets,
            'currency': row['currency'], 'cost': str(number(row['cost'], positive=True)),
            'exit_fee': str(number(row['exit_fee'])), 'target_pct': str(number(row['target_pct'], positive=True)),
            'transfer_loss_pct': str(loss)}


class PositionBook:
    def __init__(self, path):
        self.path = Path(path)

    def read(self):
        if not self.path.exists():
            return []
        try:
            data = json.loads(self.path.read_text('utf-8'))
            if data['version'] != 1 or not isinstance(data['positions'], list):
                raise ValueError()
            rows = [validate_position(row) for row in data['positions']]
            if len({r['id'] for r in rows}) != len(rows):
                raise ValueError()
            return rows
        except Exception:
            raise UserError('持仓记录格式有误；原文件已保留，请检查 positions.json') from None

    def add(self, row):
        position = validate_position(dict(row, id=uuid.uuid4().hex))
        rows = self.read() + [position]
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temp = self.path.with_suffix('.tmp')
        try:
            with temp.open('w', encoding='utf-8') as stream:
                json.dump({'version': 1, 'positions': rows}, stream, ensure_ascii=False, indent=2)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temp, self.path)
        finally:
            if temp.exists():
                temp.unlink()
        return position


def valuation_math(raw_balance, decimals, quoted_raw, quote_decimals, position, slippage):
    cost = number(position['cost'], positive=True)
    gross = Decimal(quoted_raw) / 10**quote_decimals
    slip = number(slippage, positive=True)
    if slip > 20:
        raise UserError('估值滑点不超过 20%')
    minimum_raw = int(Decimal(quoted_raw) * (100 - slip) / 100)
    minimum = Decimal(minimum_raw) / 10**quote_decimals
    net = minimum - number(position['exit_fee'])
    profit = net - cost
    pct = profit / cost * 100
    return {'holdings': Decimal(raw_balance) / 10**decimals,
            'gross': gross, 'minimum': minimum, 'net': net, 'profit': profit,
            'pct': pct, 'reached': pct >= number(position['target_pct'], positive=True)}


def estimate_exit(engine, position):
    """Quote aggregated holdings at one pinned block; perform no asset movement."""
    position = validate_position(position)
    engine.check_stop()
    block = engine.w3.eth.get_block('latest')
    if abs(time.time() - block['timestamp']) > 90:
        raise UserError('估值区块已过期，请检查节点和本机时间')
    n = block['number']
    token = engine.token(position['token'])
    decimals = engine.decimals(token)
    raw = 0
    for wallet in position['wallets']:
        engine.check_stop()
        raw += token.functions.balanceOf(wallet).call(block_identifier=n)
    quote_address = SETTLEMENTS[position['currency']] or engine.wbnb
    if token.address == quote_address:
        raise UserError('持仓代币和结算资产相同，无需退出报价')
    quote_decimals = 18 if position['currency'] == 'BNB' else engine.decimals(engine.token(quote_address))
    effective = int(Decimal(raw) * (100 - number(position['transfer_loss_pct'])) / 100)
    paths = [[token.address, quote_address]]
    if token.address != engine.wbnb and quote_address != engine.wbnb:
        paths.append([token.address, engine.wbnb, quote_address])
    best_raw, best_path = -1, None
    if effective:
        for path in paths:
            engine.check_stop()
            try:
                quoted = engine.router.functions.getAmountsOut(effective, path).call(block_identifier=n)[-1]
            except ContractLogicError:
                continue
            except Web3RPCError as exc:
                # Only a verifiable EVM revert indicates an unavailable route.
                error = getattr(exc, 'rpc_response', {}) or {}
                if isinstance(error, dict) and error.get('error', {}).get('code') == 3:
                    continue
                raise
            if quoted > best_raw:
                best_raw, best_path = quoted, path
        if best_raw <= 0:
            raise UserError('没有可用的 V2 退出报价；不推测盈利')
    else:
        best_raw = 0
    result = valuation_math(raw, decimals, best_raw, quote_decimals, position, engine.limits.slippage)
    return dict(result, block=n, path=best_path or [], effective=Decimal(effective) / 10**decimals,
                currency=position['currency'], token=token.address,
                quote_address=SETTLEMENTS[position['currency']])
