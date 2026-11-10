// data/pages.json · data/activity.json 을 읽어 '갈래 탭'으로 목록을 그린다. 문구는 전부 데이터에서만 온다.
(async function () {
  const feed = document.getElementById("feed");
  const tablist = document.getElementById("rail-list");

  const esc = (s) =>
    String(s ?? "").replace(/[&<>"']/g, (c) => ({
      "&": "&amp;",
      "<": "&lt;",
      ">": "&gt;",
      '"': "&quot;",
      "'": "&#39;",
    }[c]));

  const pad = (n) => String(n).padStart(2, "0");

  const grab = async (path) => {
    try {
      const res = await fetch(path, { cache: "no-cache" });
      return res.ok ? await res.json() : null;
    } catch (err) {
      return null;
    }
  };

  const data = await grab("data/pages.json");
  if (!data) {
    feed.innerHTML =
      '<p>목록을 불러오지 못했다. <a href="https://github.com/kimhs950627?tab=repositories">깃허브 프로필</a>에서 확인할 수 있다.</p>';
    return;
  }
  const activity = await grab("data/activity.json");

  const groups = (data.groups || []).filter((g) => g.pages && g.pages.length);
  const total = groups.reduce((sum, g) => sum + g.pages.length, 0);

  document.getElementById("stat-total").textContent = pad(total);
  document.getElementById("stat-groups").textContent = pad(groups.length);
  document.getElementById("stat-links").textContent = pad(data.link_total || 0);

  const newest = groups
    .flatMap((g) => g.pages)
    .map((p) => p.updated)
    .filter(Boolean)
    .sort()
    .pop();
  if (newest) document.getElementById("stat-updated").textContent = newest;

  document.getElementById("rail-generated").textContent = `${total}개 · ${data.generated_at || ""}`;
  document.getElementById("foot-generated").textContent = `auto-index · ${data.generated_at || ""}`;

  const changes = ((activity && activity.changes) || []).slice(0, 30);

  // 목록 각 줄에 '새 페이지 · 수정' 표시를 붙이기 위한 저장소별 최신 표시
  const FRESH_DAYS = 7;
  const stamps = new Map();
  for (const c of (activity && activity.changes) || []) {
    const fresh = c.kind === "listed" || c.kind === "new-repo" || c.kind === "pages-on";
    if (c.kind !== "push" && !fresh) continue;
    const at = c.at || "";
    const prev = stamps.get(c.repo);
    if (!prev || at > prev.at || (at === prev.at && fresh && !prev.fresh)) {
      stamps.set(c.repo, { at, fresh });
    }
  }

  const daysSince = (iso) => {
    const t = new Date(`${iso}T00:00:00+09:00`).getTime();
    return Number.isNaN(t) ? null : Math.floor((Date.now() - t) / 86400000);
  };

  const badgeFor = (repo) => {
    const stamp = stamps.get(repo);
    if (!stamp) return "";
    const days = daysSince(stamp.at);
    if (stamp.fresh) {
      return days === null || days > FRESH_DAYS
        ? ""
        : '<span class="chip-tag is-new">새 페이지</span>';
    }
    if (days === null) return "";
    if (days <= 0) return '<span class="chip-tag is-push">오늘 수정</span>';
    if (days > FRESH_DAYS) return "";
    return `<span class="chip-tag is-push">수정 ${days}일 전</span>`;
  };
  const KIND = {
    push: "수정",
    "new-repo": "새 저장소",
    "pages-on": "페이지 공개",
    "pages-off": "페이지 중단",
    visibility: "공개 범위",
    archive: "보관",
    deleted: "삭제",
    listed: "색인 등재",
  };
  const TONE = { push: "is-push", "new-repo": "is-new", "pages-on": "is-new", listed: "is-new" };

  let n = 0;

  const changesPanel = changes.length
    ? `<div class="group-head" style="--i:0">
          <h2>최근 변경</h2>
          <span class="c">${changes.length}건</span>
        </div>
        ${changes
          .map((c, k) => {
            const tone = TONE[c.kind] || "is-flip";
            return `
        <div class="item">
          <a class="row" href="${esc(c.url)}" target="_blank" rel="noopener" style="--i:${k + 1}">
            <span class="idx">${esc((c.at || "").slice(5))}</span>
            <span class="row-main">
              <span class="row-title"><span class="chip-tag ${tone}">${esc(KIND[c.kind] || c.kind)}</span>${esc(c.text)}</span>
              <span class="row-host">${esc(c.repo)}</span>
            </span>
            <span class="go" aria-hidden="true">↗</span>
          </a>
        </div>`;
          })
          .join("")}`
    : "";

  const groupPanels = groups.map((g) => {
    const rows = g.pages
      .map((p, k) => {
        const ask = p.locked
          ? `<a class="chip chip-request" href="${esc(data.request_url || "")}">열람 신청</a>`
          : "";
        const sub = (p.links || []).length || p.locked
          ? `<div class="row-sub">${p.links
              .map((l) => {
                const tag =
                  l.status === "reviewed"
                    ? '<span class="chip-tag is-reviewed">검토본</span>'
                    : l.status === "draft"
                      ? '<span class="chip-tag is-draft">초안</span>'
                      : "";
                return `<a class="chip" href="${esc(l.url)}" target="_blank" rel="noopener">${tag}<span class="chip-label">${esc(l.label)}</span><span aria-hidden="true">↗</span></a>`;
              })
              .join("")}${ask}</div>`
          : "";
        return `
        <div class="item">
          <a class="row" href="${esc(p.url)}" target="_blank" rel="noopener" style="--i:${k + 1}">
            <span class="idx">${pad(++n)}</span>
            <span class="row-main">
              <span class="row-title">${badgeFor(p.repo)}${esc(p.title)}${
                p.locked ? '<span class="lock" title="열람 승인 필요">비공개</span>' : ""
              }</span>
              <span class="row-note">${esc(p.note)}</span>
              <span class="row-host">${esc(p.host)}${
                p.locked ? " · 승인한 이메일만 열림" : ""
              }</span>
            </span>
            <span class="go" aria-hidden="true">↗</span>
          </a>
          ${sub}
        </div>`;
      })
      .join("");

    return `<div class="group-head" style="--i:0">
          <h2>${esc(g.label)}</h2>
          <span class="c">${g.pages.length}개</span>
        </div>
        ${rows}`;
  });

  const tabs = [];
  if (changesPanel) tabs.push({ id: "activity", label: "최근 변경", count: changes.length, panel: changesPanel });
  groups.forEach((g, k) =>
    tabs.push({ id: g.id, label: g.label, count: g.pages.length, panel: groupPanels[k] }),
  );

  if (!tabs.length) {
    feed.innerHTML = '<p class="empty">아직 실린 페이지가 없다.</p>';
    return;
  }

  tablist.innerHTML = tabs
    .map(
      (t, k) => `
      <button class="tab" type="button" role="tab" id="tab-${esc(t.id)}"
        aria-controls="panel-${esc(t.id)}" aria-selected="${k === 0 ? "true" : "false"}"
        tabindex="${k === 0 ? "0" : "-1"}">
        <span class="label">${esc(t.label)}</span><span class="c">${t.count}</span>
      </button>`,
    )
    .join("");

  feed.innerHTML = tabs
    .map(
      (t, k) => `
      <section class="group-panel" id="panel-${esc(t.id)}" role="tabpanel"
        aria-labelledby="tab-${esc(t.id)}"${k === 0 ? "" : " hidden"}>
        ${t.panel}
      </section>`,
    )
    .join("");

  const buttons = [...tablist.querySelectorAll('[role="tab"]')];

  const activate = (id, { focus = false, push = false } = {}) => {
    const tab = tabs.find((t) => t.id === id) || tabs[0];
    buttons.forEach((b) => {
      const on = b.id === `tab-${tab.id}`;
      b.setAttribute("aria-selected", on ? "true" : "false");
      b.tabIndex = on ? 0 : -1;
      b.classList.toggle("is-active", on);
    });
    tabs.forEach((t) => {
      document.getElementById(`panel-${t.id}`).hidden = t.id !== tab.id;
    });
    if (focus) document.getElementById(`tab-${tab.id}`).focus();
    if (push) history.replaceState(null, "", `#${tab.id}`);
    requestAnimationFrame(() => feed.classList.add("is-in"));
  };

  tablist.addEventListener("click", (event) => {
    const button = event.target.closest('[role="tab"]');
    if (button) activate(button.id.replace(/^tab-/, ""), { push: true });
  });

  tablist.addEventListener("keydown", (event) => {
    const current = buttons.findIndex((b) => b === document.activeElement);
    if (current < 0) return;
    const move = { ArrowRight: 1, ArrowDown: 1, ArrowLeft: -1, ArrowUp: -1 };
    let next = current;
    if (event.key in move) next = (current + move[event.key] + buttons.length) % buttons.length;
    else if (event.key === "Home") next = 0;
    else if (event.key === "End") next = buttons.length - 1;
    else return;
    event.preventDefault();
    activate(tabs[next].id, { focus: true, push: true });
  });

  const fromHash = () => (location.hash || "").replace(/^#(panel-)?/, "");
  activate(fromHash() || tabs[0].id);
})();
