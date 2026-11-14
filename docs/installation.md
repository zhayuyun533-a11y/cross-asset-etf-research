# 在 Codex 中安装

准备一个可在 Codex 中打开的本地项目目录，以及 **Python 3.11 或更新版本**。本发布版在 Python 3.14 上验证。首次安装回测环境需要访问 Python 包索引，或已有一套适合当前操作系统和 Python 版本的离线安装包。

## 1. 放好 Skill 文件夹

[下载 Skill ZIP](../cross-asset-etf-research.zip?raw=1)并解压。把整个 `cross-asset-etf-research` 文件夹复制到目标项目的 `.agents/skills/` 中，保留 `assets`、`references` 和 `scripts` 等子文件夹。若项目还没有 `.agents/skills/`，先创建它。

```text
你的项目/
└── .agents/
    └── skills/
        └── cross-asset-etf-research/
            ├── SKILL.md
            ├── assets/
            ├── references/
            └── scripts/
```

在 Codex 中打开这个项目。如果当前会话没识别到新 Skill，重新打开项目或开始新会话。[Codex 官方 Skill 安装位置](https://learn.chatgpt.com/docs/build-skills#where-codex-loads-local-skills)

如果使用 GitHub 仓库源码，直接在 Codex 中打开仓库根目录即可。源码里已经包含 `.agents/skills/cross-asset-etf-research`。

## 2. 安装回测环境

在项目目录中用以下命令创建一个新的回测环境。先用版本命令确认所选解释器。以下命令以 Python 3.14 为例。

**macOS 或 Linux**

```bash
python3 --version
python3 .agents/skills/cross-asset-etf-research/scripts/setup_runtime.py .venv
```

**Windows PowerShell**

```powershell
py -3.14 --version
py -3.14 .agents/skills/cross-asset-etf-research/scripts/setup_runtime.py .venv
```

脚本会输出环境中的 Python 路径。若 `.venv` 已存在，给新环境换一个目录名。无网络时，可为 pip 提供本地依赖包目录。相关命令见[离线安装说明](../.agents/skills/cross-asset-etf-research/references/runtime.md)。

## 3. 先跑合成示例

把下面的请求发给 Codex。

> 用 $cross-asset-etf-research 跑合成示例，并用通俗的话解释年化收益、最大回撤和恢复情况。告诉我结果在哪里。

成功时，Skill 会保存生成的 `prices.csv` 和含回测结果的 `results/`。在 `results/run_manifest.json` 中可以看到输入类型 `synthetic`。如果想从终端验证安装，也可以在项目目录运行下面的命令。

**macOS 或 Linux**

```bash
mkdir -p runs
.venv/bin/python -m etf_backtest.demo runs/my-first-demo
```

**Windows PowerShell**

```powershell
New-Item -ItemType Directory -Path runs -Force
& ".venv\Scripts\python.exe" -m etf_backtest.demo runs/my-first-demo
```

再次运行时，请换一个新的输出目录名。

## 4. 读结果和复跑

| 文件 | 内容 |
| --- | --- |
| `performance.csv` | 0.10% 主情景下的累计收益、年化收益、年化波动率和最大回撤 |
| `fee_sensitivity.csv` | 各档费率下的年化收益和两组收益差 |
| `drawdown_events.csv` | 各策略的峰值日期、谷值日期、恢复日和恢复用时 |
| `drawdowns.csv` | 逐日回撤 |
| `run_manifest.json` | 输入类型、日期、费率、价格文件指纹和可用的来源记录 |
| `fee_comparison.csv` | 指定额外费率时，对照 0.10% 的年化收益、最大回撤和恢复情况 |

CSV 里的收益和回撤用小数表示，`-0.05` 即 `−5%`。真实行情比较请保留本次 `prices.csv`、来源记录和运行清单。用同一份行情复跑费率或结果时，选择新的输出目录。

[返回首页，继续做 SPY、IEF 和 GLD 的历史比较](../README.md#2-比较真实历史行情)
