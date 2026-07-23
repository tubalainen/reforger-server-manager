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
//   mods: [{ mod_id, name, persist, orphaned, templates, instances,
//            provides_scenarios }]
//   tree: { edges: { modId: [depId] }, names: { modId: name },
//           types: { modId: { kind, tags } } }  (may be empty / offline)
// Each node carries a `kind` (scenario|terrain|addon, from the Workshop) and
// `tags` (its category labels) so the UI can show what a mod actually is (#131).
export function buildForest(mods, tree) {
  const edges = (tree && tree.edges) || {}
  const names = (tree && tree.names) || {}
  const types = (tree && tree.types) || {}
  const registry = new Map((mods || []).map((m) => [m.mod_id, m]))

  // `ancestors` guards against dependency cycles: a mod is never expanded
  // underneath itself, so a bad edge can't spin the builder forever.
  function node(id, ancestors) {
    const reg = registry.get(id)
    const type = types[id] || {}
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
      kind: type.kind || null,
      tags: Array.isArray(type.tags) ? type.tags : [],
      provides_scenarios: reg ? !!reg.provides_scenarios : false,
      children,
    }
  }

  return (mods || []).map((m) => node(m.mod_id, new Set()))
}

// Every modId in a forest, deduplicated — the ids a "select all" would tick.
export function allModIds(forest) {
  return subtreeIds({ modId: null, children: forest || [] }).filter((id) => id !== null)
}

// A node's own id plus every id beneath it, deduplicated — so ticking a parent
// can cascade the selection to its whole dependency subtree (#131). Cycles are
// already broken by buildForest, so the walk terminates.
export function subtreeIds(node) {
  const ids = new Set()
  const walk = (n) => {
    ids.add(n.modId)
    ;(n.children || []).forEach(walk)
  }
  walk(node)
  return [...ids]
}

// Build the path key for a node: the '>'-joined chain of modIds from its root.
// Deps are deduped per parent, so sibling modIds don't collide; the same mod
// under two different parents gets two distinct paths. This is the key the tree
// uses to remember which nodes are expanded (#131).
export function nodePath(parentPath, modId) {
  return parentPath ? `${parentPath}>${modId}` : modId
}

// Every expandable node's path (i.e. nodes that have children) — what
// "Expand all" opens. Uses the same path scheme as nodePath.
export function expandablePaths(forest) {
  const paths = new Set()
  const walk = (node, path) => {
    if (!node.children || !node.children.length) return
    paths.add(path)
    for (const child of node.children) walk(child, nodePath(path, child.modId))
  }
  ;(forest || []).forEach((n) => walk(n, n.modId))
  return paths
}
