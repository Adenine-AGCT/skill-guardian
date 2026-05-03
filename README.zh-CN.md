# Skill Guardian 技能审计与更新治理

[English README](./README.md)

`Skill Guardian 技能审计与更新治理` 是一个可安装、可发布的 GitHub Skill，专门用来在更新本地 Agent Skills 之前先做审计和风险判断。它会帮助你看清本地 skills 的来源、上游变化、更新风险，以及当前到底应该直接更新、人工复核、先阻断，还是暂时不动。

## 先安装这个 Skill

先通过 GitHub CLI 安装已发布的 skill：

```powershell
gh skill install Adenine-AGCT/skill-guardian
```

安装完成后，就可以在需要审查本地 skills 的时候直接调用它，而不是盲目更新。

## 这个 Skill 能帮你做什么

安装后，Skill Guardian 主要帮你完成这些判断：

- 盘点本地到底安装了哪些 skills
- 识别哪些 skills 能追溯到明确的 GitHub 上游
- 在可联网时对比本地版本和上游新版本
- 识别脚本变更、可执行文件新增、来源不明等风险信号
- 把一堆审查结果收束成明确的更新建议

## 使用后你会看到什么

一次典型审查结束后，最重要的是顶部决策摘要，例如：

- `No action`：当前不需要更新
- `Safe to update`：存在低风险更新，可以优先更新
- `Review required`：存在需要人工审查的更新
- `Blocked`：存在高风险变化，不建议直接应用

每个 skill 的细项结果还会包含：

- `trust_score`
- `risk_level`
- `update_recommendation`
- `confidence_explainer`

它的定位是“技能审计与更新治理”，不是“自动更新器”。

## 适合哪些场景

- 你的本地已经装了不少 skills，想先盘点清楚再决定是否更新
- 你想知道某个 skill 是不是来自可信上游，而不是来源不明的本地副本
- 你不想盲目覆盖本地 skill，希望先看风险评估和更新建议
- 你想在离线环境里也先完成本地体检

## 本地 CLI 后端

这个仓库同时提供了驱动该 skill 的 CLI / Python 后端。

如果你希望直接从本地仓库运行，可以这样开始：

```powershell
$env:PYTHONPATH = ".\src"
python -m skill_guardian audit --offline --write-lock
```

初始化用户配置：

```powershell
$env:PYTHONPATH = ".\src"
python -m skill_guardian config init
```

查看当前发现了哪些 skill 根目录：

```powershell
$env:PYTHONPATH = ".\src"
python -m skill_guardian roots list
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

也可以在 `config.json` 中配置自己的上游映射：

```json
{
  "upstreams": {
    "my-skill": {
      "repo": "owner/repo",
      "path": "skills/my-skill",
      "ref": "main"
    }
  }
}
```

## 安全边界

Skill Guardian 默认采取保守策略：

- 不自动更新本地 skills
- 不在审查过程中执行远端脚本
- 对 `scripts/` 变更和可执行文件新增保持高敏感
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
gh skill publish --tag v0.1.2
gh skill preview Adenine-AGCT/skill-guardian skill-guardian
```

发布完成后，继续使用 GitHub 的 `v*` 标签保护规则来保证版本标签不可随意篡改。
