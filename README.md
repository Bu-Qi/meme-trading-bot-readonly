# meme交易机器人（只读版）

Windows 本地的 BSC 持仓与盈利提醒工具。**本仓库没有钱包私钥导入、签名、批量买卖、资产归集或自动卖出功能。**

## 功能

- 登记多个公开钱包地址，按同一区块读取代币余额。
- 以 BNB、USDT 或 USDC 估算退出价值。
- 比较 PancakeSwap V2 直连路径与经过 WBNB 的路径。
- 手动查看退出预览，或达到目标收益率时弹窗提醒一次。
- 通过 RPC 方法白名单阻止交易提交方法。

## 运行

需要 Windows、Python 3.13（带 Tkinter）及联网的 BSC HTTPS RPC。

```powershell
python -m venv .venv
.venv\Scripts\python -m pip install -r requirements.txt
.venv\Scripts\python app.py
```

也可以在安装依赖后双击 `启动.bat`。

界面只保留四个操作：**保存持仓、退出预览、盈利提醒、停止**。

## 使用

1. 填写 RPC、目标代币的完整合约地址和公开钱包地址。
2. 填写实际投入、结算币种、目标收益率、退出费用与归集损耗预留。
3. 点击“保存持仓”。修改参数后再次保存会建立新记录，不会覆盖原记录。
4. 选中记录，查看退出预览，或启动收益目标提醒。

提醒轮询间隔为 5–3600 秒，默认 15 秒。目标和成本以保存的记录为准，滑点和轮询间隔在每次启动时读取。提醒需要程序持续运行；重启后不会自动恢复。

## 估值边界

估算收益率 =（扣除滑点和人工费用预留后的退出估值 − 登记成本）÷ 登记成本。

成本、代币税损耗及费用预留由用户登记。程序不自动跟踪买入成本或资金流出，也不预测税费、实际到账量及未来成交价格。达到提醒目标不保证实际盈利。多个记录独立估值，不应将包含重复地址的记录相加。

只支持 PancakeSwap V2，不支持 V3、Infinity 或资金操作。

## 数据与隐私

- 仅输入公开地址，**不要输入私钥、助记词或钱包密码**。
- 默认数据目录：`%APPDATA%\MemeHoldingsReadOnly`。
- `positions.json` 明文保存地址和成本，属于个人财务记录，请妥善保管。
- RPC 服务商会收到公开地址及合约查询；RPC 地址不持久化。
- 原始 RPC 错误不显示，避免泄露含密钥的节点 URL。
- `.gitignore` 排除持仓文件、钱包库、日志、环境文件及构建产物。

## 验证与打包

```powershell
python -m unittest discover -s tests -v
python app.py --smoke-test --data-dir work/smoke
python -m pip install -r requirements-build.txt
python build_windows.py
```

EXE 生成于 `dist`。源码窗口启动和离线测试不等于 EXE 的交互式验收；当前没有完成新版 EXE 的交互式验收。

## 接口参考

- [PancakeSwap V2 Router](https://developer.pancakeswap.finance/contracts/v2/router-v2)
- [PancakeSwap V2 合约地址](https://developer.pancakeswap.finance/contracts/v2/addresses)

## 仓库内容

`app.py` 为简化桌面界面，`core.py` 只包含查询接口，`positions.py` 处理持仓记录和估值，`tests/` 为离线测试。仓库不包含个人持仓、钱包库、凭证或旧版交易执行代码。
