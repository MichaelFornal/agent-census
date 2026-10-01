// Technique pages' data (written by S9), with prevalence joined in from facts.json.
import { series } from "./facts";

export interface Card { repo: string | null; path: string; kind: string; permalink: string | null; excerpt: string | null }
export interface Technique {
  id: string; label: string; category: string; definition: string; detection: string; private: boolean; evidence: Card[];
}

export const CATEGORY_LABELS: Record<string, string> = {
  hooks: "Hooks", orchestration: "Subagents", memory: "Memory and context", skills: "Skills",
  commands: "Commands", integrations: "MCP and plugins", permissions: "Permissions",
};

export const techniques: Technique[] = Object.values(
  import.meta.glob<{ default: Technique }>("../data/techniques/*.json", { eager: true }),
).map((m) => m.default);

const prevalence = new Map(
  series<{ technique_id: string; harnesses: number; share: number }>("technique_prevalence")
    .map((r) => [r.technique_id, r]),
);

export function prevalenceOf(id: string): { harnesses: number; share: number } | undefined {
  return prevalence.get(id);
}
