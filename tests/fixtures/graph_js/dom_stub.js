// Minimal DOM stand-in for the graph scripts (graph_legend.js, entity_views/*.js) when run under `node` by
// tests/test_graph_key.py and tests/test_ego_focus_js.py: elements keep attributes, classes, children, text and
// listeners (events bubble to the parent unless stopPropagation() is called); no layout, no CSS.
class FakeNode {
    constructor(tag) { this.tag = tag; this.attrs = {}; this.children = []; this.parentNode = null; this.listeners = {}; this.own = ""; this.style = {}; this.clientWidth = 900; }
    setAttribute(name, value) { this.attrs[name] = String(value); }
    getAttribute(name) { return name in this.attrs ? this.attrs[name] : null; }
    removeAttribute(name) { delete this.attrs[name]; }
    get classList() {
        const names = () => (this.attrs.class || "").split(/\s+/).filter(Boolean);
        return {
            add: (name) => { if (!names().includes(name)) { this.attrs.class = names().concat(name).join(" "); } },
            remove: (name) => { this.attrs.class = names().filter((n) => n !== name).join(" "); },
            contains: (name) => names().includes(name),
        };
    }
    get firstChild() { return this.children[0] || null; }
    get textContent() { return this.own + this.children.map((c) => c.textContent).join(""); }
    set textContent(value) { this.children.forEach((c) => { c.parentNode = null; }); this.children = []; this.own = String(value); }
    set innerHTML(value) { this.textContent = value; }
    appendChild(child) { if (child.parentNode) { child.parentNode.removeChild(child); } child.parentNode = this; this.children.push(child); return child; }
    insertBefore(child, ref) { if (!ref) { return this.appendChild(child); } if (child.parentNode) { child.parentNode.removeChild(child); } child.parentNode = this; this.children.splice(this.children.indexOf(ref), 0, child); return child; }
    get nextSibling() { const siblings = this.parentNode ? this.parentNode.children : []; return siblings[siblings.indexOf(this) + 1] || null; }
    removeChild(child) { this.children = this.children.filter((c) => c !== child); child.parentNode = null; return child; }
    addEventListener(type, fn) { (this.listeners[type] = this.listeners[type] || []).push(fn); }
    removeEventListener(type, fn) { this.listeners[type] = (this.listeners[type] || []).filter((f) => f !== fn); }
    fire(type, extra) {
        const event = Object.assign({ type, target: this, stopped: false, stopPropagation() { this.stopped = true; }, preventDefault() {} }, extra);
        for (let node = this; node && !event.stopped; node = node.parentNode) { (node.listeners[type] || []).slice().forEach((fn) => fn(event)); }
        return event;
    }
}

function makeDom() {
    const document = new FakeNode("#document");
    document.createElement = (tag) => new FakeNode(tag);
    document.createElementNS = (ns, tag) => new FakeNode(tag);
    return { document };
}

// Every descendant of root, depth first.
function all(root) {
    return root.children.reduce((list, child) => list.concat([child], all(child)), []);
}

module.exports = { makeDom, all };
