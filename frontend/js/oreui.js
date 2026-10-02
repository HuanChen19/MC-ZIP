/* Ore UI behavior for the local app and standalone browser frontend. */
"use strict";
const ORE = (() => {
  const $ = (s, r = document) => r.querySelector(s);
  const $$ = (s, r = document) => [...r.querySelectorAll(s)];
  const Theme = {
    MODES: ["light", "dark", "system"],
    LABELS: { light: "亮色", dark: "暗色", system: "自动" },
    mode: localStorage.getItem("oreui-theme-mode") || "system",
    mq: matchMedia("(prefers-color-scheme: dark)"),
    _listeners: [],
    resolved() { return this.mode === "system" ? (this.mq.matches ? "dark" : "light") : this.mode; },
    apply() {
      const theme = this.resolved();
      document.documentElement.dataset.theme = theme;
      $$(".theme-switch .ore-tab").forEach(button => {
        const selected = button.dataset.themeMode === this.mode;
        button.classList.toggle("sel", selected);
        button.setAttribute("aria-pressed", String(selected));
      });
      $("#themeNote").textContent = `主题: ${this.LABELS[this.mode]}`;
      this._listeners.forEach(fn => fn(theme));
    },
    set(mode) {
      if (!this.MODES.includes(mode)) return;
      this.mode = mode;
      localStorage.setItem("oreui-theme-mode", mode);
      this.apply();
    },
    onChange(fn) { this._listeners.push(fn); },
    init() {
      const query = new URLSearchParams(location.search).get("theme");
      if (query && this.MODES.includes(query)) this.mode = query;
      if (!this.MODES.includes(this.mode)) this.mode = "system";
      this.mq.addEventListener("change", () => this.apply());
      $$(".theme-switch .ore-tab").forEach(button => button.addEventListener("click", () => this.set(button.dataset.themeMode)));
      this.apply();
    },
  };
  function initToggles(onFlip) {
    $$(".ore-check").forEach(toggle => {
      toggle.addEventListener("click", () => {
        if (toggle.disabled) return;
        const on = toggle.getAttribute("aria-checked") !== "true";
        toggle.setAttribute("aria-checked", String(on));
        if (onFlip) onFlip(toggle, on);
      });
    });
  }
  function setToggle(toggle, on) { toggle.setAttribute("aria-checked", String(!!on)); }
  function getToggle(toggle) { return toggle.getAttribute("aria-checked") === "true"; }
  let openMenu = null, menuAnchor = null;
  function closeMenu(restoreFocus = false) {
    if (openMenu) {
      openMenu.remove();
      openMenu = null;
      menuAnchor?.setAttribute("aria-expanded", "false");
      if (restoreFocus) menuAnchor?.focus();
      menuAnchor = null;
    }
  }
  document.addEventListener("pointerdown", event => {
    if (openMenu && !event.target.closest(".ore-menu") && !event.target.closest("[data-menu-anchor]")) closeMenu();
  });
  document.addEventListener("keydown", event => { if (event.key === "Escape" && openMenu) closeMenu(true); });
  window.addEventListener("resize", () => closeMenu());
  function showMenu(anchor, items) {
    closeMenu();
    const menu = document.createElement("div");
    menu.className = "ore-menu";
    menu.setAttribute("role", "menu");
    for (const item of items) {
      if (item === "sep") {
        const separator = document.createElement("div");
        separator.className = "ore-menu-sep";
        separator.setAttribute("role", "separator");
        menu.appendChild(separator);
        continue;
      }
      const button = document.createElement("button");
      button.className = "ore-menu-item";
      button.type = "button";
      button.disabled = !!item.disabled;
      button.setAttribute("role", item.checked !== undefined ? "menuitemradio" : "menuitem");
      if (item.checked !== undefined) button.setAttribute("aria-checked", String(!!item.checked));
      if (item.danger) button.classList.add("danger");
      if (item.checked) {
        const check = document.createElement("span");
        check.className = "ore-icon icon-check menu-check";
        check.setAttribute("aria-hidden", "true");
        button.appendChild(check);
      }
      const label = document.createElement("span");
      label.textContent = item.label;
      button.appendChild(label);
      button.addEventListener("click", () => { closeMenu(true); if (item.onPick) item.onPick(); });
      menu.appendChild(button);
    }
    document.body.appendChild(menu);
    const rect = anchor.getBoundingClientRect();
    menu.style.minWidth = Math.min(Math.max(240, rect.width), innerWidth - 16) + "px";
    const width = menu.offsetWidth, height = menu.offsetHeight;
    menu.style.left = Math.max(8, Math.min(rect.left, innerWidth - width - 8)) + "px";
    menu.style.top = Math.max(8, rect.bottom + height + 4 > innerHeight - 8 ? rect.top - height - 4 : rect.bottom + 4) + "px";
    openMenu = menu;
    menuAnchor = anchor;
    anchor.setAttribute("aria-expanded", "true");
    const enabled = $$("button:enabled", menu);
    enabled[0]?.focus();
    menu.addEventListener("keydown", event => {
      if (!["ArrowDown", "ArrowUp", "Home", "End"].includes(event.key)) return;
      event.preventDefault();
      const current = enabled.indexOf(document.activeElement);
      const next = event.key === "Home" ? 0 : event.key === "End" ? enabled.length - 1 :
        (current + (event.key === "ArrowDown" ? 1 : -1) + enabled.length) % enabled.length;
      enabled[next]?.focus();
    });
    return menu;
  }
  function bindDropdown({ el, values, get, set, onChange }) {
    el.setAttribute("data-menu-anchor", "");
    el.setAttribute("aria-haspopup", "menu");
    el.setAttribute("aria-expanded", "false");
    function render() { $(".dd-label", el).textContent = get(); }
    el.addEventListener("click", () => {
      if (menuAnchor === el) { closeMenu(true); return; }
      showMenu(el, values.map(value => ({
        label: value, checked: value === get(),
        onPick: () => { set(value); render(); if (onChange) onChange(value); },
      })));
    });
    render();
    return { render };
  }
  const overlay = () => $("#overlay");
  let popupResult = null, popupFocus = null;
  function popupOpen() {
    closeMenu();
    popupFocus = document.activeElement;
    overlay().classList.add("open");
    $(".app-shell").inert = true;
    $("#popupOk").focus();
  }
  function popupClose(value = false) {
    if (!overlay().classList.contains("open")) return;
    overlay().classList.remove("open");
    $(".app-shell").inert = false;
    $("#popupCancel").style.display = "";
    if (popupResult) { const resolve = popupResult; popupResult = null; resolve(value); }
    popupFocus?.focus();
  }
  function confirmDialog({ title, body, okLabel = "确定", cancelLabel = "取消" }) {
    return new Promise(resolve => {
      $("#popupTitle").textContent = title;
      $("#popupBody").textContent = body;
      $("#popupBody").style.display = "";
      $("#popupViewer").style.display = "none";
      $("#popupOk").textContent = okLabel;
      $("#popupCancel").textContent = cancelLabel;
      $("#popupCancel").style.display = "";
      popupResult = resolve;
      popupOpen();
    });
  }
  function viewerDialog(title, text) {
    $("#popupTitle").textContent = title;
    $("#popupBody").style.display = "none";
    $("#popupViewer").style.display = "";
    $("#popupViewer").textContent = text;
    $("#popupOk").textContent = "关闭";
    $("#popupCancel").style.display = "none";
    popupOpen();
  }
  function initPopup() {
    $("#popupOk").addEventListener("click", () => popupClose(true));
    $("#popupCancel").addEventListener("click", () => popupClose(false));
    $("#popupCross").addEventListener("click", () => popupClose(false));
    overlay().addEventListener("click", event => { if (event.target === overlay()) popupClose(false); });
    document.addEventListener("keydown", event => {
      if (!overlay().classList.contains("open")) return;
      if (event.key === "Escape") { event.preventDefault(); popupClose(false); }
      if (event.key === "Tab") {
        const controls = $$("button", overlay()).filter(el => !el.disabled && el.offsetParent !== null);
        const first = controls[0], last = controls[controls.length - 1];
        if (event.shiftKey && document.activeElement === first) { event.preventDefault(); last.focus(); }
        if (!event.shiftKey && document.activeElement === last) { event.preventDefault(); first.focus(); }
      }
    });
  }
  function initTooltip() {
    const tooltip = $("#tooltip");
    document.addEventListener("mouseover", event => {
      const target = event.target.closest("[data-tooltip]");
      tooltip.classList.toggle("show", !!target);
      if (target) tooltip.textContent = target.dataset.tooltip;
    });
    document.addEventListener("mousemove", event => {
      tooltip.style.left = Math.min(event.clientX + 14, innerWidth - tooltip.offsetWidth - 8) + "px";
      tooltip.style.top = Math.min(event.clientY + 14, innerHeight - tooltip.offsetHeight - 8) + "px";
    });
    document.addEventListener("pointerdown", () => tooltip.classList.remove("show"));
  }
  function toast(message) {
    const box = $("#toasts"), element = document.createElement("div");
    element.className = "ore-toast";
    const check = document.createElement("span");
    check.className = "ore-icon icon-info";
    check.setAttribute("aria-hidden", "true");
    const label = document.createElement("span");
    label.textContent = message;
    element.append(check, label);
    box.appendChild(element);
    setTimeout(() => { element.classList.add("bye"); setTimeout(() => element.remove(), 200); }, 2800);
    while (box.children.length > 4) box.firstChild.remove();
  }
  function initScrollers() {
    $$(".ore-scroll").forEach(scroller => {
      scroller._layout = () => {};
      scroller._scrollToBottom = () => { scroller.scrollTop = scroller.scrollHeight; };
    });
  }
  function layoutScrollers() {}
  return {
    Theme, initToggles, setToggle, getToggle, bindDropdown, showMenu, closeMenu,
    initPopup, popupOpen, popupClose, confirmDialog, viewerDialog,
    initTooltip, toast, initScrollers, layoutScrollers,
  };
})();
