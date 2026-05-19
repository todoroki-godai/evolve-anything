"""LSP導入効果測定スクリプト。
baseline（scripts/lsp_baseline.json）と現在のtool呼び出し頻度を比較して効果を表示する。
Usage: python3 scripts/lsp_measure.py [--sessions N]
"""
import json, os, glob, argparse
from collections import defaultdict

BASELINE_PATH = os.path.join(os.path.dirname(__file__), "lsp_baseline.json")
BASE = os.path.expanduser("~/.claude/projects")
LSP_TOOLS = {"goToDefinition", "findReferences", "hover", "documentSymbol",
             "workspaceSymbol", "goToImplementation", "prepareCallHierarchy",
             "incomingCalls", "outgoingCalls"}

PROJECTS = {
    "rl-anything":   "-Users-todoroki-tools-rl-anything",
    "docs-platform": "-Users-todoroki-work-docs-platform",
    "sys-bots":      "-Users-todoroki-work-sys-bots",
    "figma-to-code": "-Users-todoroki-work-figma-to-code",
}


def collect(slug: str, n: int) -> tuple[dict, int]:
    files = sorted(glob.glob(f"{BASE}/{slug}/*.jsonl"), key=os.path.getmtime)[-n:]
    counts: dict = defaultdict(int)
    for f in files:
        try:
            for line in open(f):
                d = json.loads(line)
                if d.get("type") == "assistant":
                    for block in d.get("message", {}).get("content", []):
                        if isinstance(block, dict) and block.get("type") == "tool_use":
                            counts[block["name"]] += 1
        except Exception:
            pass
    return dict(counts), len(files)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--sessions", type=int, default=10)
    args = parser.parse_args()

    with open(BASELINE_PATH) as f:
        baseline = json.load(f)

    print(f"\n=== LSP 効果測定 (直近{args.sessions}セッション vs ベースライン) ===")
    print(f"ベースライン記録日: {baseline['recorded_at']}\n")

    for pj, slug in PROJECTS.items():
        b_data = baseline["projects"].get(pj, {})
        b_tool = b_data.get("tool_calls", {})
        b_n = b_data.get("sessions_sampled", 1)

        current, a_n = collect(slug, args.sessions)
        lsp_calls = sum(current.get(t, 0) for t in LSP_TOOLS)

        def ps(d, n, k): return round(d.get(k, 0) / max(n, 1), 1)

        print(f"[{pj}]  Before:{b_n}s / After:{a_n}s")
        for tool in ["Read", "Bash"]:
            b = ps(b_tool, b_n, tool)
            a = ps(current, a_n, tool)
            diff = round(a - b, 1)
            sign = "+" if diff > 0 else ""
            bar = "▼" if diff < -5 else ("▲" if diff > 5 else "→")
            print(f"  {tool:<6} {b:>6}/s → {a:>6}/s  {sign}{diff:>6}  {bar}")
        print(f"  LSP呼び出し: {lsp_calls}", "(✅ 活用中)" if lsp_calls > 0 else "(⏳ まだゼロ)")
        print()


if __name__ == "__main__":
    main()
