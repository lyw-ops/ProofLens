# Lean Formalization Assistant Agent

`lean-agent` 是一个面向 Lean 形式化项目的研究助手原型。它帮助研究者读取 Lean 项目结构、检查论文与源码一致性、生成 theorem/lemma 说明、导出 AI4Math benchmark items，并执行基础复现审计。

这个版本刻意使用 Python 标准库实现，不依赖网络或外部 API。它适合作为一个可运行的本地工具基线，后续可以继续接入 LLM、Lean LSP、GitHub 或论文写作工作流。

## 功能

- 扫描 Lean 文件，提取 `def`、`lemma`、`theorem`、`structure`、`class`、`instance` 等声明。
- 生成 proof pipeline 概览，包括 imports、声明列表和近似依赖关系。
- 对比 LaTeX/Markdown 论文中的 Lean 引用、GitHub 链接、commit hash 与本地源码。
- 为 Lean declaration 生成自然语言说明，用于 README、论文 appendix 或 artifact 文档。
- 将 Lean statements 导出为 benchmark items，包含描述、statement、依赖、难度估计和验证命令。
- 审计项目可复现性，检查 `lean-toolchain`、`lakefile`、README、`lake build` 状态等。

## 快速开始

```bash
PYTHONPATH=src python3 -m lean_agent scan examples/sample_project
PYTHONPATH=src python3 -m lean_agent explain examples/sample_project --symbol Sample.add_zero_twice
PYTHONPATH=src python3 -m lean_agent check-paper --lean-root examples/sample_project --paper examples/sample_paper.tex
PYTHONPATH=src python3 -m lean_agent benchmark examples/sample_project --out benchmark.jsonl
PYTHONPATH=src python3 -m lean_agent audit examples/sample_project
```

如果安装为命令行工具：

```bash
python3 -m pip install -e .
lean-agent scan path/to/lean/project
```

## 命令

### `scan`

扫描 Lean 项目并输出结构概览。

```bash
lean-agent scan path/to/project --format markdown
lean-agent scan path/to/project --format json --out analysis.json
```

### `explain`

为某个 theorem、lemma 或 definition 生成自然语言说明。

```bash
lean-agent explain path/to/project --symbol MyProject.Main.final_theorem
lean-agent explain path/to/project --symbol final_theorem --language en
```

### `check-paper`

检查论文中的 Lean 引用和 GitHub 链接是否与本地项目一致。

```bash
lean-agent check-paper --lean-root path/to/project --paper paper/main.tex
```

它会检查：

- `\lean{...}`、`\leanref{...}`、`\leanname{...}`、`\uses{...}` 中引用的声明是否存在。
- GitHub `blob/.../file.lean#Lx` 链接是否指向存在的本地文件。
- GitHub 链接是否固定到 40 位 commit hash。
- 链接中的 commit hash 是否与当前本地 `HEAD` 一致。

### `benchmark`

导出 AI4Math benchmark items。

```bash
lean-agent benchmark path/to/project --out benchmark.jsonl
lean-agent benchmark path/to/project --format json --out benchmark.json
```

每个 item 包含：

- `name`
- `kind`
- `file`
- `line`
- `natural_language_description`
- `lean_statement`
- `dependencies`
- `difficulty`
- `verification`

### `audit`

检查 Lean 项目可复现性。

```bash
lean-agent audit path/to/project
lean-agent audit path/to/project --run-build --timeout 120
```

`--run-build` 会调用 `lake build`。如果本机没有 Lean/Lake，报告会保留错误信息而不会中断 CLI。

## 设计定位

这个 agent 的核心不是替代 Lean，而是把研究者日常需要反复手工同步的对象连接起来：

- Lean 源码中的 formal statements。
- 论文中的数学叙述、theorem 名称、源码链接。
- README、artifact instruction 和复现实验说明。
- AI4Math benchmark 所需的结构化条目。

因此它更像一个 Lean-aware research assistant：先做可靠的静态检查和结构整理，再把需要语言生成的部分压缩成可审计、可追溯的上下文。
