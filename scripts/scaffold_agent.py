from __future__ import annotations

import argparse
from pathlib import Path


def main() -> int:
    parser = argparse.ArgumentParser(description="Create a new Agent manifest and prompt skeleton.")
    parser.add_argument("--id", required=True, help="Agent id and directory name.")
    parser.add_argument("--name", required=True, help="Human-readable Agent name.")
    parser.add_argument("--layer", default="custom", help="Agent layer, for example personal or shared-capability.")
    parser.add_argument("--owner-scope", default="", help="Optional owner scope for private personal agents.")
    args = parser.parse_args()

    root = Path(__file__).resolve().parents[1]
    agent_dir = root / "workspace" / "agents" / args.id
    agent_dir.mkdir(parents=True, exist_ok=True)
    manifest = agent_dir / "agent.yaml"
    if not manifest.exists():
        manifest.write_text(
            "\n".join(
                [
                    f"id: {args.id}",
                    f"name: {args.name}",
                    f"layer: {args.layer}",
                    f"owner_scope: {args.owner_scope}",
                    "personality: ",
                    "model: \"\"",
                    "dm_scope: per-peer",
                    "extra_system: ",
                    "prompt:",
                    f"  dir: agents/{args.id}",
                    "  use_global_files: true",
                    "  skills_enabled: true",
                    "tools:",
                    "  mode: allowlist",
                    "  names:",
                    "    - get_current_time",
                    "memory:",
                    "  enabled: true",
                    "  auto_recall: true",
                    "  top_k: 3",
                    "routing:",
                    "  intent: ",
                    "  aliases: []",
                    "  keywords: []",
                    "  reason: ",
                    "  next: ",
                    "  persist_delegate_history: false",
                    "contract:",
                    "  examples: []",
                    "",
                ]
            ),
            encoding="utf-8",
        )
    for filename, title in [
        ("IDENTITY.md", args.name),
        ("SOUL.md", "工作方式"),
        ("TOOLS.md", "工具使用"),
    ]:
        path = agent_dir / filename
        if not path.exists():
            path.write_text(f"# {title}\n\n待补充。\n", encoding="utf-8")
    print(agent_dir)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
