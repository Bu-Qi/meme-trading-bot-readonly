"""meme交易机器人 — read-only holdings and profit alerts."""
import argparse
import os
import queue
import re
import threading
import tkinter as tk
from pathlib import Path
from tkinter import ttk, messagebox

from core import Engine, Limits, UserError, address, number
from positions import PositionBook, SETTLEMENTS, estimate_exit


class App:
    def __init__(self, root, data_dir):
        self.root = root
        self.book = PositionBook(Path(data_dir) / 'positions.json')
        self.events, self.stop = queue.Queue(), threading.Event()
        self.busy, self.closing = False, False
        root.title('meme交易机器人 · 只读持仓与盈利提醒')
        root.geometry('1050x780')
        root.minsize(900, 700)
        shell = ttk.Frame(root, padding=20)
        shell.pack(fill='both', expand=True)
        ttk.Label(shell, text='meme交易机器人', font=('Microsoft YaHei UI', 22, 'bold')).pack(anchor='w')
        ttk.Label(shell, text='只读持仓与盈利提醒 · 无需私钥 · 不执行资金操作').pack(anchor='w', pady=(0, 12))
        self.values = {}
        defaults = {'rpc': 'https://bsc-dataseed.bnbchain.org', 'name': '持仓1', 'token': '',
                    'currency': 'USDT', 'cost': '100', 'exit_fee': '0', 'target_pct': '10',
                    'transfer_loss_pct': '0', 'slippage': '1', 'interval': '15'}
        self.values = {k: tk.StringVar(value=v) for k, v in defaults.items()}
        self.field(shell, 'RPC 节点', 'rpc')
        self.field(shell, '代币合约', 'token')
        row = ttk.Frame(shell)
        row.pack(fill='x', pady=5)
        for label, key, width in [('名称', 'name', 12), ('实际投入', 'cost', 12), ('目标收益 %', 'target_pct', 7)]:
            self.field(row, label, key, width, inline=True)
        ttk.Label(row, text='结算币').pack(side='left', padx=6)
        ttk.Combobox(row, textvariable=self.values['currency'], values=list(SETTLEMENTS), state='readonly', width=7).pack(side='left')
        row = ttk.Frame(shell)
        row.pack(fill='x', pady=5)
        for label, key, width in [('退出费用预留', 'exit_fee', 9), ('归集损耗 %', 'transfer_loss_pct', 6),
                                  ('滑点 %', 'slippage', 6), ('轮询秒', 'interval', 6)]:
            self.field(row, label, key, width, inline=True)
        ttk.Label(shell, text='公开钱包地址（每行一个，或用逗号分隔）').pack(anchor='w', pady=(10, 3))
        self.wallets = tk.Text(shell, height=3, font=('Consolas', 10), undo=False)
        self.wallets.pack(fill='x')
        row = ttk.Frame(shell)
        row.pack(fill='x', pady=12)
        for text, command in [('保存持仓', self.save), ('退出预览', lambda: self.run(False)),
                               ('盈利提醒', lambda: self.run(True)), ('停止', self.halt)]:
            ttk.Button(row, text=text, command=command).pack(side='left', padx=(0, 8))
        self.status = tk.StringVar(value='填写持仓并保存，然后选择记录查看预览或启动提醒。')
        ttk.Label(shell, textvariable=self.status, wraplength=950).pack(side='bottom', fill='x', pady=6)
        ttk.Label(shell, text='成本和费用均使用所选结算币；估值不等于成交收益。提醒需保持程序运行。', wraplength=950).pack(side='bottom', fill='x')
        self.tree = ttk.Treeview(shell, columns=('name', 'currency', 'cost', 'holdings', 'net', 'pct'), show='headings', height=7)
        for key, text, width in [('name', '持仓', 120), ('currency', '结算币', 75), ('cost', '投入', 100),
                                  ('holdings', '持仓数量', 170), ('net', '净退出估值', 170), ('pct', '收益率 %', 120)]:
            self.tree.heading(key, text=text)
            self.tree.column(key, width=width, minwidth=60)
        self.tree.pack(fill='both', expand=True)
        self.tree.bind('<<TreeviewSelect>>', self.select)
        self.safe(self.refresh)
        root.protocol('WM_DELETE_WINDOW', self.close)
        root.after(100, self.poll)

    def field(self, parent, label, key, width=None, inline=False):
        row = parent if inline else ttk.Frame(parent)
        if not inline:
            row.pack(fill='x', pady=4)
        ttk.Label(row, text=label).pack(side='left', padx=(0, 6))
        ttk.Entry(row, textvariable=self.values[key], width=width or 50).pack(side='left', fill='none' if inline else 'x', expand=not inline, padx=(0, 10))

    def safe(self, task):
        try:
            task()
        except UserError as exc:
            messagebox.showerror('未完成', str(exc), parent=self.root)
        except Exception as exc:
            messagebox.showerror('未完成', f'{type(exc).__name__}：请检查输入、文件权限或 RPC。', parent=self.root)

    def refresh(self):
        self.tree.delete(*self.tree.get_children())
        for p in self.book.read():
            self.tree.insert('', 'end', iid=p['id'], values=(p['name'], p['currency'], p['cost'], '—', '—', '—'))

    def select(self, _):
        if self.busy or len(self.tree.selection()) != 1:
            return
        chosen = self.tree.selection()[0]
        def task():
            p = next(r for r in self.book.read() if r['id'] == chosen)
            for key in ('name', 'token', 'currency', 'cost', 'exit_fee', 'target_pct', 'transfer_loss_pct'):
                self.values[key].set(p[key])
            self.wallets.delete('1.0', 'end')
            self.wallets.insert('1.0', '\n'.join(p['wallets']))
        self.safe(task)

    def save(self):
        def task():
            if self.busy:
                raise UserError('请先停止当前任务')
            fields = {k: self.values[k].get().strip() for k in ('name', 'token', 'currency', 'cost', 'exit_fee', 'target_pct', 'transfer_loss_pct')}
            fields['wallets'] = [address(v) for v in re.split(r'[\s,;，；]+', self.wallets.get('1.0', 'end').strip()) if v]
            p = self.book.add(fields)
            self.refresh()
            self.tree.selection_set(p['id'])
            self.status.set('已保存。修改参数后再次保存会建立新记录。')
        self.safe(task)

    def run(self, watch):
        def launch():
            if self.busy:
                raise UserError('请先停止当前任务')
            selected = self.tree.selection()
            if len(selected) != 1:
                raise UserError('请选择一条已保存持仓')
            p = next(r for r in self.book.read() if r['id'] == selected[0])
            interval = float(number(self.values['interval'].get(), positive=True))
            if not 5 <= interval <= 3600:
                raise UserError('轮询间隔为 5–3600 秒')
            self.stop.clear()
            engine = Engine(self.values['rpc'].get().strip(), Limits(number(self.values['slippage'].get())), self.stop)
            self.busy = True
            self.status.set('正在读取主网持仓和 V2 报价…')
            def worker():
                try:
                    engine.connect()
                    while not self.stop.is_set():
                        result = estimate_exit(engine, p)
                        self.events.put(('value', p, result))
                        if not watch or result['reached']:
                            self.events.put(('alert' if watch else 'preview', p, result))
                            break
                        self.stop.wait(interval)
                except UserError as exc:
                    self.events.put(('error', str(exc), None))
                except Exception as exc:
                    self.events.put(('error', f'{type(exc).__name__}：读取失败，请检查 RPC 和合约。未推测盈利。', None))
                finally:
                    self.events.put(('done', None, None))
            threading.Thread(target=worker, daemon=True).start()
        self.safe(launch)

    def poll(self):
        try:
            while True:
                kind, p, r = self.events.get_nowait()
                if kind == 'value':
                    self.tree.item(p['id'], values=(p['name'], p['currency'], p['cost'], f'{r["holdings"]:.10g}', f'{r["net"]:.10g}', f'{r["pct"]:.4f}'))
                    self.status.set(f'区块 {r["block"]} · 收益率估值 {r["pct"]:.4f}% · 目标 {p["target_pct"]}%')
                elif kind in ('preview', 'alert') and not self.closing:
                    if kind == 'alert':
                        self.root.bell()
                    info = (f'代币：{p["token"]}\n结算：{p["currency"]} {SETTLEMENTS[p["currency"]] or "原生资产"}\n'
                            f'持仓：{r["holdings"]:.12g}\n净退出估值：{r["net"]:.12g} {p["currency"]}\n'
                            f'利润估值：{r["profit"]:.12g} {p["currency"]}（{r["pct"]:.4f}%）\n'
                            f'参考路径：{" → ".join(r["path"]) or "无持仓"}\n\n仅估值和提醒，没有执行任何资金操作。')
                    messagebox.showinfo('达到收益目标' if kind == 'alert' else '退出预览', info, parent=self.root)
                elif kind == 'error':
                    self.status.set(p)
                elif kind == 'done':
                    self.busy = False
                    if self.closing:
                        self.root.destroy()
                        return
        except queue.Empty:
            pass
        self.root.after(100, self.poll)

    def halt(self):
        self.stop.set()
        self.status.set('停止请求已提交，等待当前网络读取结束。')

    def close(self):
        if self.busy:
            self.closing = True
            self.halt()
        else:
            self.root.destroy()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--data-dir', type=Path, default=Path(os.environ.get('APPDATA', str(Path.home()))) / 'MemeHoldingsReadOnly')
    parser.add_argument('--smoke-test', action='store_true')
    args = parser.parse_args()
    args.data_dir.mkdir(parents=True, exist_ok=True)
    # Windows lock prevents competing writes from duplicate desktop instances.
    import msvcrt
    lock = (args.data_dir / 'instance.lock').open('a+b')
    if lock.seek(0, 2) == 0:
        lock.write(b'0')
        lock.flush()
    lock.seek(0)
    root = tk.Tk()
    root.withdraw()
    try:
        msvcrt.locking(lock.fileno(), msvcrt.LK_NBLCK, 1)
    except OSError:
        messagebox.showerror('meme交易机器人', '该数据目录已有实例在运行。', parent=root)
        root.destroy()
        lock.close()
        return
    try:
        App(root, args.data_dir)
        root.deiconify()
        if args.smoke_test:
            root.after(500, root.destroy)
        root.mainloop()
    finally:
        lock.close()


if __name__ == '__main__':
    main()
