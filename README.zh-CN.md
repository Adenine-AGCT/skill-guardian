# skill-guardian

[English README](./README.md)

`skill-guardian` 是一个面向 Agent Skills 的安全优先审查工具。
它会扫描本地 skill 目录、识别已知上游来源、在可联网时对比远端候选版本、输出可解释的信任评分和更新风险，并在最后告诉你哪些 skill 可以更新、哪些需要人工审阅、哪些应当阻断。

这个仓库同时提供两种交付形态：

- 一个可复用的 Python CLI
- 一个可发布到 GitHub Skills 的 Agent Skill，位于 `skills/skill-guardian/`

## 核心能力

- 自动发现 `~/.codex/skills`、`~/.agents/skills` 以及自定义根目录
- 对本地 skill 做结构与行为审查
- 对已知 GitHub upstream 做版本检查与差异判断
- 输出可解释的 `trust_score`、`risk_level`、`update_recommendation`
- 支持离线体检
- 支持 `markdown`、`json`、`sarif` 输出
- 运行时状态写入用户配置目录，而不是 skill 安装目录

## 快速开始

直接从仓库运行：

```powershell
$env:PYTHONPATH = ".\src"
python -m skill_guardian audit --offline --write-lock
```

初始化配置：

```powershell
$env:PYTHONPATH = ".\src"
python -m skill_guardian config init
```

查看当前发现的 skill 根目录：

```powershell
$env:PYTHONPATH = ".\src"
python -m skill_guardian roots list
```

## 默认扫描目录

- `~/.codex/skills`
- `~/.agents/skills`
- `config.json` 中声明的额外根目录

## 状态目录

运行时状态默认保存在用户配置目录中，而不是已安装的 skill 目录里。

- Windows：`%APPDATA%/skill-guardian/`
- macOS：`~/Library/Application Support/skill-guardian/`
- Linux：`${XDG_CONFIG_HOME:-~/.config}/skill-guardian/`

其中包含：

- `config.json`
- `skills.lock.json`
- `history.jsonl`

你也可以通过环境变量 `SKILL_GUARDIAN_STATE_DIR` 指定状态目录。

## CLI 命令

主要命令有：

- `skill-guardian audit`
- `skill-guardian config init`
- `skill-guardian roots list`
- `skill-guardian mappings sync`

示例：

```powershell
$env:PYTHONPATH = ".\src"
python -m skill_guardian audit --policy balanced --format markdown --write-lock
```

## 配置自定义 upstream

在 `config.json` 中配置：

```json
{
  "roots": [],
  "upstreams": {
    "skill-guardian": {
      "repo": "owner/repo",
      "path": "skills/skill-guardian",
      "ref": "main"
    }
  },
  "policy": "balanced",
  "source_allowlist": [],
  "source_blocklist": []
}
```

## 风险评分说明

每个 skill 会输出四类核心判断：

- `trust_score`
  0 到 100 的综合信任分
- `risk_level`
  `low`、`medium`、`high`、`critical`
- `update_recommendation`
  `update`、`review`、`block`、`skip`
- `confidence_explainer`
  解释为什么会得到当前结论

推荐规则大致如下：

- `update`
  远端变化主要是文档或低风险元数据
- `review`
  变化涉及 `SKILL.md`、`agents/openai.yaml` 或关键说明文件
- `block`
  变化涉及 `scripts/`、可执行文件、危险模式或可疑提示覆盖
- `skip`
  无上游、无变化或证据不足

## 作为 Skill 使用

可发布 skill 位于 `skills/skill-guardian/`。
其包装脚本会默认执行：

```text
audit --format markdown --write-lock
```

这意味着首次触发时会直接给出面向用户阅读的体检摘要，而不是原始 JSON。

## 开发与发布

安装依赖后，可先运行测试：

```powershell
python -m unittest discover -s .\tests -v
```

在发布 skill 前，同步 bundled runtime：

```powershell
python .\scripts\sync_skill_runtime.py
python .\scripts\sync_skill_runtime.py --check
```

之后进入 skill 目录做发布前校验：

```powershell
cd .\skills\skill-guardian
gh skill preview
```

检查通过后再发布：

```powershell
gh skill publish
```

## 发布前清理检查

发布前建议确认：

- 仓库中没有 `.tmp-tests/`
- 仓库中没有 `.skill-guardian-state/`
- 仓库中没有 `__pycache__` 或 `.pyc`
- `skills/skill-guardian/` 只保留发布所需文件

## 安全边界

`skill-guardian` 是一个审查器，不是自动更新器。
它默认：

- 不自动更新本地 skill
- 不执行远端脚本
- 不把运行状态写回已安装 skill 目录
- 对高风险变化给出显式解释

“安全”在这个项目里表示“默认尽量保守并且可解释”，而不是对任何第三方 skill 做绝对安全保证。
