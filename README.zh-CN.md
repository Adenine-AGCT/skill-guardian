# Skill Guardian 技能审计与更新治理

[English README](./README.md)

`Skill Guardian 技能审计与更新治理` 是一个可安装、可发布的 GitHub Skill，专门用于在更新本地 Agent Skills 之前先做治理审查。它默认走轻量路径：先做快速审计，优先判断来源、基线和本地漂移，只在确有必要时才升级为更深入的远端检查。

## 先安装这个 Skill

先通过 GitHub CLI 安装已发布的 skill：

```powershell
gh skill install Adenine-AGCT/skill-guardian
```

安装完成后，就可以在需要审查本地 skills 的时候直接调用它，而不是盲目更新。

## 为什么它不会浪费很多 Tokens

Skill Guardian 的设计重点之一，就是避免每次触发都进入重型审查：

- 发布版 skill 默认运行 `quick` 模式
- 先做本地 provenance、baseline、drift 检查，再决定是否升级
- 只有出现高价值信号时，才进入更深的远端比较
- 默认 markdown 输出是简报，只有重点 skill 才展开细节

也就是说，它的目标不是“每次都最全面”，而是“多数时候足够轻，关键时候足够深”。

## 这个 Skill 能帮你做什么

安装后，Skill Guardian 主要帮助你完成这些判断：

- 盘点本地到底安装了哪些 skills
- 识别哪些 skills 有明确的 GitHub 上游
- 检测某个 skill 是否偏离了上次可信基线
- 判断本地版本和 GitHub 版本是否已经拉开较大差距
- 识别脚本改动、可执行文件新增、来源不明等高风险信号
- 把这些信息收束成明确的更新建议

## 使用后你会看到什么

一次典型审查结束后，顶部会先给出简洁结论，例如：

```text
Overall action: Review required
Mode: quick
Local drift detected: analytics-skill
Large version gap: deploy-skill
Review required: analytics-skill
```

每个重点 skill 的结果会重点围绕这些字段展开：

- `baseline_status`
- `version_gap_level`
- `trust_score`
- `risk_level`
- `update_recommendation`
- `safe_next_step`

## 三层审计模式

Skill Guardian 现在使用三层运行模式：

- `quick`
  发布版 skill 的默认模式。优先做本地盘点、来源识别、基线和漂移检查，只在必要时使用轻量远端元数据。
- `standard`
  对少量高价值候选项做更深入的远端版本与差异分析。
- `deep`
  面向明确要求的全量审计，做完整远端比较和更详细解释。

如果发现某个 skill 的本地版本和 GitHub 版本差距较大，运行会自动从 `quick` 升级到 `standard`。但“差距大”只代表值得重点检查，不代表一定适合立即更新。

## 本地 CLI 后端

这个仓库同时提供了驱动该 skill 的 CLI / Python 后端。

如果你希望直接从本地仓库运行，可以这样开始：

```powershell
$env:PYTHONPATH = ".\src"
python -m skill_guardian audit --offline --write-lock
```

显式运行轻量模式：

```powershell
$env:PYTHONPATH = ".\src"
python -m skill_guardian audit --mode quick
```

需要完整深审查时再运行：

```powershell
$env:PYTHONPATH = ".\src"
python -m skill_guardian audit --mode deep --max-remote-checks 999
```

初始化用户配置：

```powershell
$env:PYTHONPATH = ".\src"
python -m skill_guardian config init
```

## 发现与配置

默认会从这些位置寻找本地 skills：

- `~/.codex/skills`
- `~/.agents/skills`
- `config.json` 中声明的额外根目录

运行时状态默认保存在用户配置目录中，而不是已安装的 skill 目录里：

- Windows：`%APPDATA%/skill-guardian/`
- macOS：`~/Library/Application Support/skill-guardian/`
- Linux：`${XDG_CONFIG_HOME:-~/.config}/skill-guardian/`

其中通常包含：

- `config.json`
- `skills.lock.json`
- `history.jsonl`

如果需要自定义状态目录，可以设置环境变量 `SKILL_GUARDIAN_STATE_DIR`。

## 安全边界

Skill Guardian 默认采取保守策略：

- 不自动更新本地 skills
- 不在审查过程中执行远端脚本
- 对 `scripts/` 变更和可执行文件新增保持高敏感
- 默认优先轻量审计，只在必要时升级
- 即使远端检查失败，也会完成本地审查并明确说明远端状态不可用
- 运行状态不写回已安装的 skill 目录

## 开发与发布

最小验证流程：

```powershell
python -m unittest discover -s .\tests -v
python .\scripts\sync_skill_runtime.py --check
```

发布 bundled skill 前，先在仓库根目录同步并校验 runtime：

```powershell
python .\scripts\sync_skill_runtime.py
python .\scripts\sync_skill_runtime.py --check
```

然后进入 skill 目录执行：

```powershell
cd .\skills\skill-guardian
gh skill publish --tag v0.2.0
gh skill preview Adenine-AGCT/skill-guardian skill-guardian
```

发布完成后，继续使用 GitHub 的 `v*` 标签保护规则来保证版本标签不可随意篡改。
