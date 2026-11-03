// data/pages.json 을 읽어 목록을 그린다. 문구는 전부 데이터에서만 온다.
(async function () {
  const feed = document.getElementById("feed");

  const esc = (s) =>
    String(s ?? "").replace(/[&<>"']/g, (c) => ({
      "&": "&amp;",
      "<": "&lt;",
      ">": "&gt;",
      '"': "&quot;",
      "'": "&#39;",
    }[c]));

  const pad = (n) => String(n).padStart(2, "0");

  let data;
  try {
    const res = await fetch("data/pages.json", { cache: "no-cache" });
    if (!res.ok) throw new Error(String(res.status));
    data = await res.json();
  } catch (err) {
    feed.innerHTML =
      '<p>목록을 불러오지 못했다. <a href="https://github.com/kimhs950627?tab=repositories">깃허브 프로필</a>에서 확인할 수 있다.</p>';
    return;
  }

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

  let i = 0;
  let n = 0;

  const railList = document.getElementById("rail-list");
  railList.innerHTML = groups
    .map(
      (g) =>
        `<li><a href="#${esc(g.id)}"><span class="label">${esc(g.label)}</span><span class="c">${g.pages.length}</span></a></li>`,
    )
    .join("");

  feed.innerHTML = groups
    .map((g) => {
      const head = `
        <div class="group-head" id="${esc(g.id)}" style="--i:${i++}">
          <h2>${esc(g.label)}</h2>
          <span class="c">${g.pages.length}개</span>
        </div>`;

      const rows = g.pages
        .map((p) => {
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
          <a class="row" href="${esc(p.url)}" target="_blank" rel="noopener" style="--i:${i++}">
            <span class="idx">${pad(++n)}</span>
            <span class="row-main">
              <span class="row-title">${esc(p.title)}${
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

      return `<section class="group" aria-labelledby="h-${esc(g.id)}">
        ${head.replace("<h2>", `<h2 id="h-${esc(g.id)}">`)}
        ${rows}
      </section>`;
    })
    .join("");

  requestAnimationFrame(() => feed.classList.add("is-in"));

  // 색인에서 지금 보고 있는 갈래를 표시
  const links = [...railList.querySelectorAll("a")];
  const observer = new IntersectionObserver(
    (entries) => {
      entries.forEach((entry) => {
        if (!entry.isIntersecting) return;
        links.forEach((a) => a.classList.toggle("is-active", a.getAttribute("href") === `#${entry.target.id}`));
      });
    },
    { rootMargin: "-20% 0px -70% 0px" },
  );
  feed.querySelectorAll(".group-head").forEach((el) => observer.observe(el));
})();
