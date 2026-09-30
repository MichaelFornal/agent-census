// Every number the site prints goes through here, from facts.json (PRD §7).
import doc from "../data/facts.json";

type Fact = { id: string; value: unknown; edition_hash: string; computed_at: string; query_file: string };
const facts = (doc as unknown as { facts: Record<string, Fact> }).facts;

export const edition = doc as unknown as { edition: string; edition_hash: string; frozen_at: string };

export function scalar(id: string): number {
  const f = facts[id];
  if (!f || typeof f.value !== "number") throw new Error(`fact ${id} is missing or not a scalar`);
  return f.value;
}

export function series<T = Record<string, unknown>>(id: string): T[] {
  const f = facts[id];
  if (!f || !Array.isArray(f.value)) throw new Error(`fact ${id} is missing or not a series`);
  return f.value as T[];
}

export function fmt(value: number, format: "int" | "pct"): string {
  return format === "pct" ? `${(value * 100).toFixed(1)}%` : value.toLocaleString("en-US");
}
