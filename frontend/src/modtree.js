// Pure helpers for the Mods Overview tree (issue #131).
//
// The overview is a forest: every registered mod is a root (it was explicitly
// baked into a template), and under each root we expand the dependency subtree
// discovered live from the Workshop. A mod that is both standalone and a
// dependency of another therefore shows up twice — once as its own root, once
// nested under its parent — which is exactly what the issue asks for.
//
// Dependencies that were never registered on their own still appear as child
// nodes (flagged `registered: false`), named from the resolver when possible.

// Build the forest from the registry list and the resolved dependency edges.
//   mods: [{ mod_id, name, persist, orphaned, templates, instances }]
//   tree: { edges: { modId: [depId] }, names: { modId: name } }  (may be empty)
export function buildForest(mods, tree) {
  const edges = (tree && tree.edges) || {}
  const names = (tree && tree.names) || {}
  const registry = new Map((mods || []).map((m) => [m.mod_id, m]))

  // `ancestors` guards against dependency cycles: a mod is never expanded
  // underneath itself, so a bad edge can't spin the builder forever.
  function node(id, ancestors) {
    const reg = registry.get(id)
    const children = []
    if (!ancestors.has(id)) {
      const next = new Set(ancestors)
      next.add(id)
      for (const dep of edges[id] || []) children.push(node(dep, next))
    }
    return {
      modId: id,
      name: (reg && reg.name) || names[id] || id,
      registered: !!reg,
      persist: reg ? !!reg.persist : false,
      orphaned: reg ? !!reg.orphaned : false,
      templates: (reg && reg.templates) || [],
      instances: (reg && reg.instances) || [],
      children,
    }
  }

  return (mods || []).map((m) => node(m.mod_id, new Set()))
}

// Every modId in a forest, deduplicated — the ids a "select all" would tick.
export function allModIds(forest) {
  const ids = new Set()
  const walk = (n) => {
    ids.add(n.modId)
    n.children.forEach(walk)
  }
  ;(forest || []).forEach(walk)
  return [...ids]
}
