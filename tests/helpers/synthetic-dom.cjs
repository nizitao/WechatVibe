"use strict";
// Minimal DOM for executing scoped UI controllers against synthetic bridge responses.
function element(tag = "div", className = "", value = "") {
  let content = String(value), classes = new Set(className.split(/\s+/).filter(Boolean));
  const node = { tag, children: [], dataset: {}, attributes: {}, style: {}, listeners: {},
    hidden: false, disabled: false, value: "", focused: false,
    classList: { add: name => classes.add(name), remove: name => classes.delete(name),
      contains: name => classes.has(name), toggle(name, force = !classes.has(name)) {
        if (force) classes.add(name); else classes.delete(name); return force;
      } },
    append(...items) { this.children.push(...items); },
    appendChild(item) { this.children.push(item); return item; },
    replaceChildren(...items) { content = ""; this.children = items; },
    setAttribute(name, val) { this.attributes[name] = String(val); },
    removeAttribute(name) { delete this.attributes[name]; },
    addEventListener(name, fn) { (this.listeners[name] ||= []).push(fn); },
    fire(name, event = {}) { for (const fn of this.listeners[name] || []) fn({ target: this, ...event }); },
    focus() { this.focused = true; },
  };
  Object.defineProperties(node, {
    textContent: { get() { return content + this.children.map(item => item.textContent ?? String(item)).join(""); },
      set(val) { content = String(val); this.children = []; } },
    childNodes: { get() { return this.children; } },
    className: { get() { return [...classes].join(" "); }, set(val) { classes = new Set(String(val).split(/\s+/).filter(Boolean)); } },
  });
  return node;
}
function dom() {
  const nodes = new Map();
  const byId = id => {
    if (!nodes.has(id)) nodes.set(id, element());
    return nodes.get(id);
  };
  return { byId, element, document: { activeElement: element(), createElement: element,
    createTextNode: text => element("text", "", text) },
    text: (id, value) => { byId(id).textContent = value ?? ""; },
    status: (node, value) => { node.textContent = value; } };
}
module.exports = { element, dom };
