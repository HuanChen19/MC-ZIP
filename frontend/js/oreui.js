/* ==========================================================================
   oreui.js —— Ore UI 主题与通用组件（MC-ZIP 前端）
   - 主题：亮色 / 暗色 / 自动（localStorage + prefers-color-scheme）
   - 组件：开关、下拉框、弹窗、工具提示、Toast、自绘滚动条
   ========================================================================== */
"use strict";

const ORE = (() => {
  const $  = (s, r = document) => r.querySelector(s);
  const $$ = (s, r = document) => [...r.querySelectorAll(s)];

  /* ------------------------------------------------------------------ *
   * 主题管理
   * ------------------------------------------------------------------ */
  const Theme = {
    MODES: ["light", "dark", "system"],
    LABELS: { light: "亮色", dark: "暗色", system: "自动" },
    mode: localStorage.getItem("oreui-theme-mode") || "system",
    mq: matchMedia("(prefers-color-scheme: dark)"),
    _listeners: [],

    resolved() { return this.mode === "system" ? (this.mq.matches ? "dark" : "light") : this.mode; },

    apply() {
      const t = this.resolved();
      document.documentElement.dataset.theme = t;
      $("#favicon").href = `assets/${t}/pack.png`;
      $$(".theme-switch .ore-tab").forEach(b =>
        b.classList.toggle("sel", b.dataset.themeMode === this.mode));
      $("#themeNote").textContent = `主题：${this.LABELS[this.mode]}${this.mode === "system" ? `（当前 ${this.LABELS[t]}）` : ""}`;
      this._listeners.forEach(fn => fn(t));
    },

    set(mode) {
      this.mode = mode;
      localStorage.setItem("oreui-theme-mode", mode);
      this.apply();
    },

    onChange(fn) { this._listeners.push(fn); },

    init() {
      const q = new URLSearchParams(location.search).get("theme");
      if (q && this.MODES.includes(q)) this.mode = q;
      this.mq.addEventListener("change", () => this.apply());
      $$(".theme-switch .ore-tab").forEach(b =>
        b.addEventListener("click", () => this.set(b.dataset.themeMode)));
      this.apply();
    },
  };

  /* ------------------------------------------------------------------ *
   * 开关（亮色=Ore 菱形 / 暗色=方框，贴图自带四态）
   * ------------------------------------------------------------------ */
  function initToggles(onFlip) {
    $$(".ore-check").forEach(cb => {
      cb.addEventListener("click", () => {
        const on = cb.getAttribute("aria-checked") !== "true";
        cb.setAttribute("aria-checked", String(on));
        if (onFlip) onFlip(cb, on);
      });
    });
  }
  function setToggle(cb, on) { cb.setAttribute("aria-checked", String(!!on)); }
  function getToggle(cb) { return cb.getAttribute("aria-checked") === "true"; }

  /* ------------------------------------------------------------------ *
   * Ore 下拉框：按钮 + 弹出菜单（popup.png 九宫格）
   * ------------------------------------------------------------------ */
  let openMenu = null;
  function closeMenu() {
    if (openMenu) { openMenu.remove(); openMenu = null; }
  }
  document.addEventListener("pointerdown", e => {
    if (openMenu && !e.target.closest(".ore-menu") && !e.target.closest("[data-menu-anchor]")) closeMenu();
  });
  document.addEventListener("keydown", e => { if (e.key === "Escape") closeMenu(); });

  /**
   * 在 anchor 下方弹出菜单。
   * items: [{ label, checked?, disabled?, danger?, onPick? }] 或 "sep"
   */
  function showMenu(anchor, items) {
    closeMenu();
    const menu = document.createElement("div");
    menu.className = "ore-menu";
    for (const item of items) {
      if (item === "sep") {
        const sep = document.createElement("div");
        sep.className = "ore-menu-sep";
        menu.appendChild(sep);
        continue;
      }
      const btn = document.createElement("button");
      btn.className = "ore-menu-item";
      btn.type = "button";
      btn.disabled = !!item.disabled;
      if (item.danger) btn.classList.add("danger");
      if (item.checked) {
        const ck = document.createElement("img");
        ck.src = "assets/light/icon_check.png";
        ck.alt = "";
        ck.className = "menu-check";
        btn.appendChild(ck);
      }
      const span = document.createElement("span");
      span.textContent = item.label;
      btn.appendChild(span);
      btn.addEventListener("click", () => { closeMenu(); item.onPick && item.onPick(); });
      menu.appendChild(btn);
    }
    document.body.appendChild(menu);
    const r = anchor.getBoundingClientRect();
    const mw = menu.offsetWidth, mh = menu.offsetHeight;
    let x = Math.min(r.left, innerWidth - mw - 8);
    let y = r.bottom + 4;
    if (y + mh > innerHeight - 8) y = r.top - mh - 4;
    menu.style.left = Math.max(8, x) + "px";
    menu.style.top = Math.max(8, y) + "px";
    openMenu = menu;
    return menu;
  }

  /**
   * 绑定一个下拉选择框：button.ore-dropdown，data-options 由 JS 提供。
   * select: { el, values: [..], get(), set(v), onChange(v) }
   */
  function bindDropdown({ el, values, get, set, onChange }) {
    el.setAttribute("data-menu-anchor", "");
    function render() { $(".dd-label", el).textContent = get(); }
    el.addEventListener("click", () => {
      showMenu(el, values.map(v => ({
        label: v,
        checked: v === get(),
        onPick: () => { set(v); render(); onChange && onChange(v); },
      })));
    });
    render();
    return { render };
  }

  /* ------------------------------------------------------------------ *
   * 弹窗（确认 / manifest 查看）
   * ------------------------------------------------------------------ */
  const overlay = () => $("#overlay");
  function popupOpen() { overlay().classList.add("open"); }
  function popupClose() { overlay().classList.remove("open"); }

  /** 确认对话框：{ title, body, okLabel, cancelLabel, danger } → Promise<boolean> */
  function confirmDialog({ title, body, okLabel = "确定", cancelLabel = "取消" }) {
    return new Promise(resolve => {
      $("#popupTitle").textContent = title;
      const bodyEl = $("#popupBody");
      bodyEl.textContent = body;
      bodyEl.style.display = "";
      $("#popupViewer").style.display = "none";
      const ok = $("#popupOk"), cancel = $("#popupCancel");
      ok.textContent = okLabel;
      cancel.textContent = cancelLabel;
      popupOpen();
      const done = v => { popupClose(); ok.onclick = cancel.onclick = null; resolve(v); };
      ok.onclick = () => done(true);
      cancel.onclick = () => done(false);
      $("#popupCross").onclick = () => done(false);
    });
  }

  /** 只读文本查看器（manifest.json 内容）。 */
  function viewerDialog(title, text) {
    $("#popupTitle").textContent = title;
    $("#popupBody").style.display = "none";
    const viewer = $("#popupViewer");
    viewer.style.display = "";
    viewer.textContent = text;
    $("#popupOk").textContent = "关闭";
    $("#popupCancel").style.display = "none";
    popupOpen();
    $("#popupOk").onclick = () => { popupClose(); $("#popupCancel").style.display = ""; };
    $("#popupCross").onclick = () => { popupClose(); $("#popupCancel").style.display = ""; };
  }

  function initPopup() {
    overlay().addEventListener("click", e => { if (e.target === overlay()) popupClose(); });
    document.addEventListener("keydown", e => { if (e.key === "Escape") popupClose(); });
  }

  /* ------------------------------------------------------------------ *
   * 工具提示（跟随光标）
   * ------------------------------------------------------------------ */
  function initTooltip() {
    const tip = $("#tooltip");
    let target = null;
    document.addEventListener("mouseover", e => {
      const t = e.target.closest("[data-tooltip]");
      target = t;
      if (t) { tip.textContent = t.dataset.tooltip; tip.classList.add("show"); }
      else tip.classList.remove("show");
    });
    document.addEventListener("mousemove", e => {
      if (!target) return;
      const pad = 14;
      const r = tip.getBoundingClientRect();
      let x = e.clientX + pad, y = e.clientY + pad;
      if (x + r.width > innerWidth - 4) x = e.clientX - r.width - pad;
      if (y + r.height > innerHeight - 4) y = e.clientY - r.height - pad;
      tip.style.left = x + "px";
      tip.style.top = y + "px";
    });
    document.addEventListener("mouseleave", () => tip.classList.remove("show"), true);
  }

  /* ------------------------------------------------------------------ *
   * Toast
   * ------------------------------------------------------------------ */
  function toast(msg) {
    const box = $("#toasts");
    const el = document.createElement("div");
    el.className = "ore-toast";
    const icon = document.createElement("img");
    icon.src = "assets/light/icon_check.png";
    icon.alt = "";
    const span = document.createElement("span");
    span.textContent = msg;
    el.append(icon, span);
    box.append(el);
    setTimeout(() => {
      el.classList.add("bye");
      el.addEventListener("animationend", () => el.remove(), { once: true });
    }, 2400);
    while (box.children.length > 4) box.firstChild.remove();
  }

  /* ------------------------------------------------------------------ *
   * 自绘滚动条（scroller 贴图，滚轮 + 拖拽）
   * ------------------------------------------------------------------ */
  function initScrollers() {
    $$(".ore-scroll").forEach(sc => {
      const content = $(".scroll-content", sc);
      const track = $(".ore-track", sc);
      const thumb = $(".ore-thumb", sc);
      let scroll = 0;
      const maxScroll = () => Math.max(0, content.scrollHeight - sc.clientHeight);

      function layout() {
        const max = maxScroll();
        scroll = Math.min(scroll, max);
        content.style.transform = `translateY(${-scroll}px)`;
        if (max <= 0) { track.style.display = "none"; return; }
        track.style.display = "block";
        const viewH = sc.clientHeight;
        const thumbH = Math.max(40, viewH * viewH / content.scrollHeight);
        thumb.style.height = thumbH + "px";
        thumb.style.top = scroll * (viewH - thumbH) / max + "px";
      }
      sc._layout = layout;
      sc._scrollToBottom = () => { scroll = maxScroll(); layout(); };

      sc.addEventListener("wheel", e => {
        const max = maxScroll();
        if (max <= 0) return;
        e.preventDefault();
        scroll = Math.min(max, Math.max(0, scroll + e.deltaY));
        layout();
      }, { passive: false });

      thumb.addEventListener("pointerdown", e => {
        e.preventDefault();
        thumb.classList.add("dragging");
        thumb.setPointerCapture(e.pointerId);
        const startY = e.clientY, startScroll = scroll;
        const viewH = sc.clientHeight, thumbH = thumb.clientHeight;
        const move = ev => {
          const max = maxScroll();
          scroll = Math.min(max, Math.max(0,
            startScroll + (ev.clientY - startY) * max / (viewH - thumbH)));
          layout();
        };
        const up = () => {
          thumb.classList.remove("dragging");
          thumb.removeEventListener("pointermove", move);
          thumb.removeEventListener("pointerup", up);
        };
        thumb.addEventListener("pointermove", move);
        thumb.addEventListener("pointerup", up);
      });

      new ResizeObserver(layout).observe(sc);
      new ResizeObserver(layout).observe(content);
      layout();
    });
  }
  function layoutScrollers() { $$(".ore-scroll").forEach(sc => sc._layout && sc._layout()); }

  return {
    Theme, initToggles, setToggle, getToggle,
    bindDropdown, showMenu, closeMenu,
    initPopup, popupOpen, popupClose, confirmDialog, viewerDialog,
    initTooltip, toast, initScrollers, layoutScrollers,
  };
})();
