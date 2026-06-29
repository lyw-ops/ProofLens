from __future__ import annotations

from collections import Counter

from lean_agent.models import LeanDeclaration, ProjectAnalysis


def explain_symbol(
    analysis: ProjectAnalysis,
    symbol: str,
    language: str = "zh",
) -> str:
    declaration = resolve_symbol(analysis, symbol)
    if declaration is None:
        available = ", ".join(item.name for item in analysis.declarations[:10])
        raise KeyError(f"Symbol not found: {symbol}. Available examples: {available}")
    downstream = _downstream_users(analysis, declaration.name)
    if language == "en":
        return _explain_en(declaration, downstream)
    return _explain_zh(declaration, downstream)


def resolve_symbol(analysis: ProjectAnalysis, symbol: str) -> LeanDeclaration | None:
    declaration_map = analysis.declaration_map
    if symbol in declaration_map:
        return declaration_map[symbol]
    matches = [
        declaration
        for declaration in analysis.declarations
        if declaration.name.endswith("." + symbol) or declaration.short_name == symbol
    ]
    if len(matches) == 1:
        return matches[0]
    if matches:
        return sorted(matches, key=lambda item: (item.file, item.line))[0]
    return None


def _downstream_users(analysis: ProjectAnalysis, name: str) -> list[LeanDeclaration]:
    return [
        declaration
        for declaration in analysis.declarations
        if name in declaration.dependencies
    ]


def _explain_zh(declaration: LeanDeclaration, downstream: list[LeanDeclaration]) -> str:
    lines: list[str] = []
    lines.append(f"# `{declaration.name}`")
    lines.append("")
    lines.append(
        f"`{declaration.name}` 是 `{declaration.file}` 第 {declaration.line} 行附近的 "
        f"{_kind_zh(declaration.kind)}。"
    )
    if declaration.docstring:
        lines.append("")
        lines.append(f"源码文档说明：{declaration.docstring}")
    lines.append("")
    lines.append("## Lean statement")
    lines.append("")
    lines.append("```lean")
    lines.append(declaration.statement or declaration.source.splitlines()[0])
    lines.append("```")
    lines.append("")
    if declaration.dependencies:
        lines.append("## 在证明结构中的位置")
        lines.append("")
        lines.append(
            "它显式引用了这些已扫描到的声明："
            + ", ".join(f"`{name}`" for name in declaration.dependencies)
            + "。"
        )
    else:
        lines.append("## 在证明结构中的位置")
        lines.append("")
        lines.append("在当前静态扫描范围内，它没有直接引用其他已命名声明，可能属于基础定义、入口定理或依赖来自 imported modules。")
    if downstream:
        lines.append("")
        lines.append(
            "后续有 "
            + str(len(downstream))
            + " 个声明引用它，典型下游包括："
            + ", ".join(f"`{item.name}`" for item in downstream[:5])
            + "。"
        )
    else:
        lines.append("")
        lines.append("当前扫描范围内没有其他声明直接引用它；如果它是最终 theorem，这通常是正常现象。")
    lines.append("")
    lines.append(_role_sentence_zh(declaration, downstream))
    return "\n".join(lines)


def _explain_en(declaration: LeanDeclaration, downstream: list[LeanDeclaration]) -> str:
    lines: list[str] = []
    lines.append(f"# `{declaration.name}`")
    lines.append("")
    lines.append(
        f"`{declaration.name}` is a {declaration.kind} near line {declaration.line} "
        f"of `{declaration.file}`."
    )
    if declaration.docstring:
        lines.append("")
        lines.append(f"Source docstring: {declaration.docstring}")
    lines.append("")
    lines.append("## Lean statement")
    lines.append("")
    lines.append("```lean")
    lines.append(declaration.statement or declaration.source.splitlines()[0])
    lines.append("```")
    lines.append("")
    if declaration.dependencies:
        lines.append(
            "It explicitly refers to these scanned declarations: "
            + ", ".join(f"`{name}`" for name in declaration.dependencies)
            + "."
        )
    else:
        lines.append(
            "Within the scanned project, it does not directly refer to another named declaration. "
            "It may be foundational or depend mainly on imports."
        )
    if downstream:
        lines.append(
            f"{len(downstream)} later declarations refer to it, including "
            + ", ".join(f"`{item.name}`" for item in downstream[:5])
            + "."
        )
    else:
        lines.append("No scanned downstream declaration directly refers to it.")
    return "\n".join(lines)


def _kind_zh(kind: str) -> str:
    mapping = {
        "def": "定义",
        "abbrev": "缩写定义",
        "theorem": "定理",
        "lemma": "引理",
        "structure": "结构",
        "class": "类型类",
        "inductive": "归纳类型",
        "instance": "实例",
        "axiom": "公理",
        "constant": "常量声明",
        "opaque": "不透明定义",
        "example": "示例",
    }
    return mapping.get(kind, kind)


def _role_sentence_zh(declaration: LeanDeclaration, downstream: list[LeanDeclaration]) -> str:
    downstream_kinds = Counter(item.kind for item in downstream)
    if declaration.kind in {"theorem", "lemma"} and downstream:
        common = downstream_kinds.most_common(1)[0][0]
        return f"论文写作中，可以把它描述为支撑后续 `{common}` 的中间结果，并在 appendix 中链接到对应 Lean statement。"
    if declaration.kind in {"theorem", "lemma"}:
        return "论文写作中，可以把它描述为当前 formalization pipeline 的结论性节点，并检查正文 theorem 名称是否与该 Lean 名称一致。"
    if declaration.kind in {"def", "structure", "class", "inductive"}:
        return "论文写作中，可以把它描述为 formalization 的基础对象；benchmark 构建时，它也适合作为依赖上下文而不是单独证明目标。"
    return "它适合作为 artifact 文档中的辅助声明，帮助读者定位 Lean 实现与数学叙述之间的对应关系。"

