"""Read-only BSC RPC access. No private keys, signing or transaction submission."""
import time
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation, DefaultContext, getcontext
from urllib.parse import urlparse

from web3 import Web3
from web3.middleware import ExtraDataToPOAMiddleware

DefaultContext.prec = 100
getcontext().prec = 100
ROUTER = Web3.to_checksum_address('0x10ED43C718714eb63d5aA57B78B54704E256024E')
FACTORY = Web3.to_checksum_address('0xcA143Ce32Fe78f1f7019d7d551a6402fC5350c73')


class UserError(Exception):
    pass


def number(value, *, positive=False):
    try:
        result = Decimal(str(value))
    except (InvalidOperation, ValueError):
        raise UserError('请输入有效数字') from None
    if not result.is_finite() or result < 0 or result > Decimal('1e40') or (positive and result <= 0):
        raise UserError('请输入有效的非负数；投入金额和收益目标必须大于 0')
    return result


def address(value):
    if not Web3.is_address(value) or int(value, 16) == 0:
        raise UserError('请输入完整的非零 0x 公开地址')
    return Web3.to_checksum_address(value)


def fn(name, inputs=(), outputs=()):
    return {'type': 'function', 'name': name, 'stateMutability': 'view',
            'inputs': [{'name': f'a{i}', 'type': t} for i, t in enumerate(inputs)],
            'outputs': [{'name': f'r{i}', 'type': t} for i, t in enumerate(outputs)]}


TOKEN_ABI = [fn('decimals', outputs=['uint8']), fn('balanceOf', ['address'], ['uint256'])]
ROUTER_ABI = [fn('factory', outputs=['address']), fn('WETH', outputs=['address']),
              fn('getAmountsOut', ['uint256', 'address[]'], ['uint256[]'])]


class ReadOnlyProvider(Web3.HTTPProvider):
    ALLOWED = frozenset({'eth_chainId', 'eth_blockNumber', 'eth_getBlockByNumber',
                         'eth_getCode', 'eth_getBalance', 'eth_call', 'net_version'})

    def make_request(self, method, params):
        if method not in self.ALLOWED:
            raise UserError('只读程序禁止该 RPC 方法')
        return super().make_request(method, params)

    def make_batch_request(self, requests):
        if any(method not in self.ALLOWED for method, _ in requests):
            raise UserError('只读程序禁止该批量 RPC 请求')
        return super().make_batch_request(requests)


@dataclass(frozen=True)
class Limits:
    slippage: Decimal = Decimal('1')


class Engine:
    def __init__(self, rpc, limits, stop):
        parsed = urlparse(rpc)
        if not parsed.hostname or parsed.username or parsed.password or parsed.fragment:
            raise UserError('RPC 地址格式不正确')
        if parsed.scheme != 'https' and not (parsed.scheme == 'http' and parsed.hostname in ('127.0.0.1', 'localhost')):
            raise UserError('RPC 使用 HTTPS 或本机 HTTP')
        if not 0 < number(limits.slippage) <= 20:
            raise UserError('估值滑点须大于 0 且不超过 20%')
        self.limits, self.stop = limits, stop
        self.w3 = Web3(ReadOnlyProvider(rpc, request_kwargs={'timeout': 15}))
        self.w3.middleware_onion.inject(ExtraDataToPOAMiddleware, layer=0)
        self.router = self.w3.eth.contract(address=ROUTER, abi=ROUTER_ABI)
        self.wbnb = None

    def check_stop(self):
        if self.stop.is_set():
            raise UserError('监控已停止')

    def connect(self):
        self.check_stop()
        if self.w3.eth.chain_id != 56:
            raise UserError('RPC 必须连接 BSC 主网，chainId=56')
        block = self.w3.eth.get_block('latest')
        if abs(time.time() - block['timestamp']) > 90:
            raise UserError('RPC 区块过期，请更换节点或校准本机时间')
        if not self.w3.eth.get_code(ROUTER) or self.router.functions.factory().call() != FACTORY:
            raise UserError('PancakeSwap V2 路由校验失败')
        self.wbnb = self.router.functions.WETH().call()

    def token(self, token):
        token = address(token)
        if not self.w3.eth.get_code(token):
            raise UserError('代币地址没有合约代码')
        return self.w3.eth.contract(address=token, abi=TOKEN_ABI)

    def decimals(self, token):
        decimals = token.functions.decimals().call()
        if not 0 <= decimals <= 36:
            raise UserError('不支持该代币精度')
        return decimals
