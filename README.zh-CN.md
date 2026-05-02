# skill-guardian

[English README](./README.md)

`skill-guardian` 是一个面向 Agent Skills 的安全优先审查工具。它会扫描本地 skill 目录、识别版本来源、在可联网时检查已知 GitHub 上游，并给出清晰的更新建议，告诉你哪些 skill 可以更新、哪些需要人工审阅、哪些应当先阻断。

## 它能解决什么问题

很多本地 skill 用久了之后，最难的不是“怎么更新”，而是“我到底应不应该更新”。

`skill-guardian` 适合用来解决这些问题：

- 本地到底装了哪些 skill
- 哪些 skill 能追溯到明确的 GitHub 来源
- 本地版本和上游版本相比是否落后
- 某次更新到底只是文档变化，还是已经涉及脚本和高风险内容
- 面对来源不明或行为可疑的 skill，应该继续使用、人工复查，还是先阻断

## 快速开始

先通过 GitHub CLI 安装已发布的 skill：

```powershell
gh skill install Adenine-AGCT/skill-guardian
```

如果你是在本地仓库中直接运行，也可以这样开始：

直接从仓库运行一次本地审查：

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

## 适合谁用

- 已经装了不少本地 skill，想先盘点清楚再决定是否清理或更新的人
- 想确认某个 skill 是否来自可信上游，而不是来源不明的本地副本
- 不想盲目覆盖本地 skill，希望先看风险评估和更新建议的人
- 想在离线环境里先完成本地体检的人

## 输出结果怎么看

每个 skill 的结果都会围绕几个核心字段展开：

- `trust_score`
  0 到 100 的综合信任分，用来表达这个 skill 当前状态的可信程度
- `risk_level`
  风险等级，例如 `low`、`medium`、`high`、`critical`
- `update_recommendation`
  更新建议，可能是 `update`、`review`、`block`、`skip`
- `confidence_explainer`
  对结论的解释，说明为什么会得到当前建议

这个工具的定位是“审查与建议”，不是“自动更新器”。

## 默认会扫描哪里

默认会从这些位置寻找本地 skills：

- `~/.codex/skills`
- `~/.agents/skills`
- `config.json` 中声明的额外根目录

## 配置方式

运行时状态默认保存在用户配置目录中，而不是已安装的 skill 目录里：

- Windows：`%APPDATA%/skill-guardian/`
- macOS：`~/Library/Application Support/skill-guardian/`
- Linux：`${XDG_CONFIG_HOME:-~/.config}/skill-guardian/`

其中通常会包含：

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

## 作为 Skill 使用

这个仓库同时包含一个可发布的 Agent Skill，目录位于 `skills/skill-guardian/`。

当它作为 skill 被触发时，会调用同一套审查引擎，并输出适合直接阅读的结论摘要，而不是原始机器结果。也就是说，你既可以把它当作 CLI 工具使用，也可以把它安装成 skill 供支持 skills 的 agent 调用。

安装已发布版本的最短命令是：

```powershell
gh skill install Adenine-AGCT/skill-guardian
```

## 安全边界

`skill-guardian` 默认采取保守策略：

- 不自动更新本地 skill
- 不在审查过程中执行远端脚本
- 对 `scripts/` 变更和可执行文件新增保持高敏感
- 即使远端检查失败，也会完成本地审查并明确说明远端状态不可用
- 运行状态不写回已安装的 skill 目录

## 开发与发布

如果你是贡献者或维护者，最小验证流程可以用：

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
gh skill publish --tag v0.1.1
gh skill preview Adenine-AGCT/skill-guardian skill-guardian@v0.1.1
```

发布完成后，建议在 GitHub 仓库 Settings 中为 `v*` 标签补充 tag protection ruleset。
