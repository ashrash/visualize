export interface TreeNode {
  name: string;
  value: number;
  children?: TreeNode[];
}

/**
 * Group-by rows into a tree for treemap / sunburst: one level per entry of `levels`, leaf values
 * summed from `value`, parents the sum of their children. Blank names become "(blank)".
 * Children are found through a per-node Map, so building is linear in rows x levels.
 */
export function hierarchyToTree(o: {
  rows: Record<string, unknown>[];
  levels: string[];
  value: string;
}): TreeNode[] {
  const root: TreeNode = { name: "", value: 0, children: [] };
  const index = new Map<TreeNode, Map<string, TreeNode>>();
  for (const row of o.rows) {
    const raw = row[o.value];
    const amount = typeof raw === "number" && Number.isFinite(raw) ? raw : 0;
    let node = root;
    for (const level of o.levels) {
      const cell = row[level];
      const name = cell === null || cell === undefined || cell === "" ? "(blank)" : String(cell);
      let byName = index.get(node);
      if (!byName) {
        byName = new Map();
        index.set(node, byName);
      }
      let child = byName.get(name);
      if (!child) {
        child = { name, value: 0 };
        byName.set(name, child);
        node.children ??= [];
        node.children.push(child);
      }
      child.value += amount;
      node = child;
    }
  }
  return root.children ?? [];
}
