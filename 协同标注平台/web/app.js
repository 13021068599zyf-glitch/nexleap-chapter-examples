const PAGE_SIZE = 72;

const state = {
  meta: { image_count: 0, sources: [], statuses: {}, unassigned: 0, phases: [] },
  chapters: [],
  sceneGroups: [],
  sceneGroupEditId: null,
  days: [],
  source: "",
  day: "",
  chapter: "",
  status: "",
  phase: "",
  query: "",
  page: 1,
  listVersion: 0,
  total: 0,
  hasMore: false,
  images: [],
  selected: new Set(),
  activeImage: null,
  assignmentIds: [],
  chapterEditId: null,
  dayEditId: null,
  toastTimer: null,
  busy: false,
  importing: false,
};

const $ = (selector) => document.querySelector(selector);
const escapeHtml = (value) => String(value ?? "").replace(/[&<>"']/g, (char) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[char]);
const splitTags = (value) => String(value || "").split(/[,，、;；\n]+/).map((tag) => tag.trim()).filter(Boolean);

async function api(path, options = {}) {
  const response = await fetch(path, {
    ...options,
    headers: { "Content-Type": "application/json", ...(options.headers || {}) },
    body: options.body === undefined ? undefined : JSON.stringify(options.body),
  });
  const contentType = response.headers.get("Content-Type") || "";
  const payload = contentType.includes("application/json") ? await response.json() : null;
  if (!response.ok) throw new Error(payload?.error || `请求失败（${response.status}）`);
  return payload;
}

function notify(message) {
  const toast = $("#toast");
  toast.textContent = message;
  toast.hidden = false;
  clearTimeout(state.toastTimer);
  state.toastTimer = setTimeout(() => { toast.hidden = true; }, 2600);
}

function setBusy(busy, label = "正在保存") {
  state.busy = busy;
  const saveState = $("#saveState");
  saveState.textContent = busy ? `${label}…` : "本机已保存";
  saveState.classList.toggle("is-saving", busy);
}

async function refreshMeta() {
  state.meta = await api("/api/meta");
  const phases = state.meta.phases || [];
  if (state.phase && !phases.some((item) => String(item.phase) === state.phase)) state.phase = "";
  $("#phaseFilter").innerHTML = `<option value="">全部抽帧组</option>${phases.map((item) => `<option value="${item.phase}">偏移为${item.offset_seconds}组</option>`).join("")}`;
  $("#phaseFilter").value = state.phase;
  $("#phaseFilter").disabled = phases.length === 0;
  renderSidebar();
}

async function refreshChapters() {
  state.chapters = await api("/api/chapters");
  renderSidebar();
  renderChapterSummary();
}

async function refreshDays() {
  state.days = await api("/api/days");
  renderSidebar();
  renderDaySummary();
  renderChapterSummary();
}

async function refreshHierarchy() {
  const [days, chapters] = await Promise.all([api("/api/days"), api("/api/chapters")]);
  state.days = days;
  state.chapters = chapters;
  renderSidebar();
  renderDaySummary();
  renderChapterSummary();
}

function activeChapter() {
  return state.chapters.find((chapter) => String(chapter.chapter_id) === state.chapter) || null;
}

async function refreshSceneGroups() {
  const chapter = activeChapter();
  state.sceneGroups = chapter ? await api("/api/chapters/" + chapter.chapter_id + "/groups") : [];
}

function activeDay() {
  return state.days.find((day) => String(day.day_id) === state.day) || null;
}

function currentCoverPhase() {
  if (state.phase !== "") return Number(state.phase);
  return Number(state.meta.phases?.[0]?.phase ?? 0);
}

function currentCoverOffset() {
  return state.meta.phases?.find((item) => item.phase === currentCoverPhase())?.offset_seconds ?? 0;
}

function chapterCover(chapter, phase = currentCoverPhase()) {
  return chapter?.covers?.[String(phase)] || null;
}

function isImageGroupCover(chapter, image) {
  return Boolean(chapter && image.phase !== null && chapterCover(chapter, image.phase)?.image_id === image.image_id);
}

function chapterButton(chapter) {
  return `<div class="chapter-nav-row"><button class="nav-button chapter-button ${state.chapter === String(chapter.chapter_id) ? "active" : ""}" type="button" data-chapter="${chapter.chapter_id}"><span class="chapter-dot"></span><span class="chapter-name">${escapeHtml(chapter.title)}</span><span class="nav-count">${Number(chapter.image_count || 0)}</span></button><button class="chapter-edit-button" type="button" data-edit-chapter="${chapter.chapter_id}" aria-label="编辑 ${escapeHtml(chapter.title)}">⋯</button></div>`;
}

function renderSidebar() {
  $("#indexedCount").textContent = Number(state.meta.image_count || 0).toLocaleString("zh-CN");
  $("#sourceTotal").textContent = `${Number(state.meta.image_count || 0).toLocaleString("zh-CN")} 张`;
  $("#unassignedCount").textContent = Number(state.meta.unassigned || 0).toLocaleString("zh-CN");
  const sourceItems = [{ source: "", label: "全部图片", count: state.meta.image_count }, ...(state.meta.sources || []).map((item) => ({ source: item.source, label: item.source, count: item.count }))];
  $("#sourceNav").innerHTML = sourceItems.map((item) => `
    <button class="nav-button ${!state.day && !state.chapter && state.source === item.source ? "active" : ""}" type="button" data-source="${escapeHtml(item.source)}">
      <span class="nav-symbol">${item.source ? "◫" : "▦"}</span><span>${escapeHtml(item.label)}</span><span class="nav-count">${Number(item.count || 0).toLocaleString("zh-CN")}</span>
    </button>`).join("");
  $("#dayNav").innerHTML = state.days.map((day) => `<div class="day-group"><div class="day-nav-row"><button class="nav-button day-button ${state.day === String(day.day_id) ? "active" : ""}" type="button" data-day="${day.day_id}"><span class="nav-symbol">◫</span><span class="day-name">${escapeHtml(day.title)}</span><span class="nav-count">${Number(day.chapter_count || 0)}</span></button><button class="day-edit-button" type="button" data-edit-day="${day.day_id}" aria-label="编辑 ${escapeHtml(day.title)}">⋯</button></div><div class="day-children">${state.chapters.filter((chapter) => chapter.day_id === day.day_id).map(chapterButton).join("")}</div></div>`).join("");
  const orphanChapters = state.chapters.filter((chapter) => !chapter.day_id);
  $("#orphanChapterCount").textContent = orphanChapters.length;
  $("#orphanDayButton").classList.toggle("active", state.day === "unassigned");
  $("#chapterNav").innerHTML = orphanChapters.map(chapterButton).join("");
  $("#unassignedButton").classList.toggle("active", state.chapter === "unassigned");
  const chapter = activeChapter();
  const day = activeDay();
  $("#crumbLabel").textContent = chapter ? chapter.title : day ? day.title : state.day === "unassigned" ? "未归入 Day" : state.chapter === "unassigned" ? "尚未归入 Chapter" : state.source || "全部图片";
  $("#statusFilter").value = state.status;
  $("#statusFilterLabel").textContent = activeChapter() ? "Chapter状态" : "图片状态";
  $("#statusFilter").setAttribute("aria-label", activeChapter() ? "当前 Chapter 内筛选状态" : "图片状态筛选");
  $("#timelineNote").hidden = Boolean(chapter) || !(state.meta.phases || []).length;
}

function renderChapterSummary() {
  const chapter = activeChapter();
  const box = $("#chapterSummary");
  box.hidden = !chapter;
  if (!chapter) return;
  const cover = $("#chapterCover");
  const coverEntry = chapterCover(chapter);
  cover.hidden = !coverEntry;
  $("#chapterCoverEmpty").hidden = Boolean(coverEntry);
  $("#chapterCoverGroup").textContent = `偏移为${currentCoverOffset()}组`;
  $("#chapterCoverEmpty small").textContent = `从偏移为${currentCoverOffset()}组图片中选择`;
  if (coverEntry) cover.src = `/media/${coverEntry.image_id}`;
  const parent = state.days.find((day) => day.day_id === chapter.day_id);
  $("#chapterParentDay").textContent = parent ? `${parent.title} / Chapter` : "未归入 Day / Chapter";
  $("#chapterTitle").textContent = chapter.title;
  $("#chapterIntro").textContent = chapter.intro || "还没有填写简介。";
  $("#chapterImageCount").textContent = Number(chapter.image_count || 0);
  $("#chapterTags").innerHTML = (chapter.tags || []).map((tag) => `<span class="tag">${escapeHtml(tag)}</span>`).join("");
  $("#pageDescription").textContent = "浏览本 Chapter 的全部图片，可按终选、候选和未选分类查看；终选勾选会保留。";
}

function renderDaySummary() {
  const day = activeDay();
  const orphan = state.day === "unassigned";
  $("#daySummary").hidden = !day && !orphan;
  if (!day && !orphan) return;
  $("#dayTitle").textContent = day?.title || "未归入 Day";
  $("#dayIntro").textContent = day?.intro || (orphan ? "这些 Chapter 还没有指定所属 Day。编辑 Chapter 即可归入某一天。" : "还没有填写简介。");
  $("#editDayButton").hidden = orphan;
  $("#exportDayButton").hidden = orphan;
  $("#dayImageCount").textContent = Number(day?.image_count || 0);
  const chapters = state.chapters.filter((chapter) => day ? chapter.day_id === day.day_id : !chapter.day_id);
  $("#dayChapters").innerHTML = chapters.length ? chapters.map((chapter) => `
    <button class="day-chapter-card" type="button" data-summary-chapter="${chapter.chapter_id}">
      <span class="day-chapter-cover ${chapterCover(chapter) ? "has-cover" : ""}">
        ${chapterCover(chapter) ? `<img src="/media/${chapterCover(chapter).image_id}" alt="${escapeHtml(chapter.title)}分组封面" loading="lazy">` : `<span class="day-cover-placeholder">本组暂无封面</span>`}
        <span class="day-cover-group">偏移为${currentCoverOffset()}组</span>
      </span>
      <span class="day-chapter-card-copy"><strong>${escapeHtml(chapter.title)}</strong><small>${Number(chapter.image_count || 0)} 张图片</small></span>
    </button>`).join("") : `<span class="day-chapter-empty">暂无 Chapter</span>`;
}

function currentFilterDescription() {
  const chapter = activeChapter();
  if (chapter) return chapter.title;
  const day = activeDay();
  if (day) return day.title;
  if (state.day === "unassigned") return "未归入 Day";
  if (state.chapter === "unassigned") return "尚未归入 Chapter";
  return state.source || "全部图片";
}

function imageCard(image) {
  const chapter = activeChapter();
  const isCover = isImageGroupCover(chapter, image);
  const checked = state.selected.has(image.image_id);
  const status = image.status === "终选" ? "final" : image.status === "候选" ? "candidate" : ["排除"].includes(image.status) ? "excluded" : "unselected";
  const shownStatus = `<span class="status-badge ${status}">${escapeHtml(image.status === "未处理" ? "未处理" : image.status || "未处理")}</span>`;
  const isFinal = image.status === "终选";
  const tags = (image.tags || []).slice(0, 2).map((tag) => `<span class="tag">${escapeHtml(tag)}</span>`).join("");
  const sourceLabel = `${image.source}${image.time_label ? ` · ${image.time_label}` : ""}`;
  return `<article class="image-card ${checked ? "is-selected" : ""}" data-image-card="${image.image_id}">
    <div class="image-thumb-wrap" data-open-image="${image.image_id}">
      <img class="image-thumb" src="/media/${image.image_id}" alt="${escapeHtml(image.filename)}" loading="lazy">
      ${shownStatus}
      ${isCover ? `<span class="cover-mark">本组 Chapter 封面</span>` : ""}
      ${image.assigned ? `<span class="assigned-mark">已归入 ${image.chapter_count > 1 ? `${image.chapter_count} 个 Chapter` : "Chapter"}</span>` : ""}
    </div>
    <label class="check-wrap" aria-label="选择图片"><input type="checkbox" data-select-image="${image.image_id}" ${checked ? "checked" : ""}></label>
    <div class="image-card-body"><span class="image-title" title="${escapeHtml(image.filename)}">${escapeHtml(image.filename)}</span>
      <div class="image-subtitle"><span>${escapeHtml(sourceLabel)}</span><span>${image.phase === null ? (image.frame_index === null ? "" : `帧 ${image.frame_index}`) : `偏移为${image.offset_seconds}组`}</span></div>
      ${image.note ? `<p class="image-note" title="${escapeHtml(image.note)}">${escapeHtml(image.note)}</p>` : ""}
      ${tags ? `<div class="card-tags">${tags}</div>` : ""}
      <button class="final-toggle-button ${isFinal ? "is-final" : ""}" type="button" data-toggle-final="${image.image_id}" aria-pressed="${isFinal}">${isFinal ? "☑ 终选" : "☐ 标记为终选"}</button>
      ${chapter ? `<button class="card-cover-button ${isCover ? "is-cover" : ""}" type="button" data-set-cover="${image.image_id}" ${isCover ? "disabled" : ""}>${isCover ? "本组当前封面" : "设为本组封面"}</button>` : ""}
    </div>
  </article>`;
}

function sceneGroupSection(title, description, items, group = null) {
  const actions = group ? '<div class="scene-group-actions"><button type="button" data-edit-scene-group="' + group.group_id + '">编辑</button><button type="button" data-move-scene-group="' + group.group_id + '" data-direction="-1" aria-label="前移风貌组">↑</button><button type="button" data-move-scene-group="' + group.group_id + '" data-direction="1" aria-label="后移风貌组">↓</button></div>' : "";
  return '<section class="scene-group-section"><div class="scene-group-heading"><div><h3>' + escapeHtml(title) + '<span>' + items.length + ' 张已加载</span></h3>' + (description ? '<p>' + escapeHtml(description) + '</p>' : '') + '</div>' + actions + '</div><div class="scene-group-grid">' + items.map(imageCard).join("") + '</div></section>';
}

function renderImages(append = false, newItems = []) {
  const grid = $("#imageGrid");
  const grouped = Boolean(activeChapter());
  grid.classList.toggle("has-scene-groups", grouped);
  if (grouped) {
    const assigned = new Set();
    const sections = state.sceneGroups.map((group) => {
      const ids = new Set(group.image_ids || []);
      const items = state.images.filter((image) => ids.has(image.image_id));
      for (const image of items) assigned.add(image.image_id);
      return sceneGroupSection(group.title, group.description, items, group);
    });
    const ungrouped = state.images.filter((image) => !assigned.has(image.image_id));
    if (ungrouped.length || !sections.length) sections.push(sceneGroupSection("未分组素材", "选择图片后可归入风貌组。", ungrouped));
    grid.innerHTML = sections.join("");
  } else {
    if (!append) grid.innerHTML = "";
    grid.insertAdjacentHTML("beforeend", (append ? newItems : state.images).map(imageCard).join(""));
  }
  const hasItems = state.images.length > 0;
  $("#emptyState").hidden = hasItems;
  $("#visibleCount").textContent = Number(state.total || 0).toLocaleString("zh-CN");
  $("#loadMoreButton").hidden = !state.hasMore;
  renderSelectionBar();
}

function renderSelectionBar() {
  const hasSelection = state.selected.size > 0;
  $("#selectionBar").hidden = !hasSelection;
  $("#selectionCount").textContent = state.selected.size;
  $("#removeSelectedButton").hidden = !activeChapter();
  $("#assignGroupButton").hidden = !activeChapter();
  $("#ungroupSelectedButton").hidden = !activeChapter();
}

function imageParams(page, limit) {
  const params = new URLSearchParams({ page: String(page), limit: String(limit) });
  if (state.query) params.set("q", state.query);
  if (state.source) params.set("source", state.source);
  if (state.status) params.set("status", state.status);
  if (state.phase) params.set("phase", state.phase);
  if (state.chapter) params.set("chapter", state.chapter);
  if (state.day) params.set("day", state.day);
  return params;
}

function rememberGridPosition() {
  const cards = [...$("#imageGrid").querySelectorAll("[data-image-card]")];
  const visible = cards.find((card) => card.getBoundingClientRect().bottom > 0);
  const index = visible ? cards.indexOf(visible) : -1;
  return {
    scrollY: window.scrollY,
    top: visible?.getBoundingClientRect().top ?? 0,
    candidates: index < 0 ? [] : [
      ...cards.slice(index).map((card) => card.dataset.imageCard),
      ...cards.slice(0, index).reverse().map((card) => card.dataset.imageCard),
    ],
  };
}

function restoreGridPosition(position) {
  requestAnimationFrame(() => {
    const card = position.candidates.map((id) => document.querySelector(`[data-image-card="${id}"]`)).find(Boolean);
    if (card) window.scrollBy(0, card.getBoundingClientRect().top - position.top);
    else window.scrollTo(0, position.scrollY);
  });
}

async function refreshVisibleImages(position = rememberGridPosition()) {
  const listVersion = ++state.listVersion;
  const limit = Math.max(PAGE_SIZE, state.page * PAGE_SIZE);
  const [result] = await Promise.all([api(`/api/images?${imageParams(1, limit)}`), refreshSceneGroups()]);
  if (listVersion !== state.listVersion) return;
  state.total = result.total;
  state.hasMore = result.has_more;
  state.images = result.items;
  state.selected.clear();
  renderImages();
  renderChapterSummary();
  renderDaySummary();
  if (state.day === "unassigned") $("#dayImageCount").textContent = Number(state.total || 0);
  restoreGridPosition(position);
}

async function loadImages({ reset = true } = {}) {
  if (reset) {
    state.listVersion += 1;
    state.page = 1;
    state.images = [];
    state.selected.clear();
    $("#imageGrid").innerHTML = `<div class="loading-state">正在整理图片…</div>`;
  }
  const listVersion = state.listVersion;
  try {
    const [result] = await Promise.all([api(`/api/images?${imageParams(state.page, PAGE_SIZE)}`), refreshSceneGroups()]);
    if (listVersion !== state.listVersion) return;
    state.total = result.total;
    state.hasMore = result.has_more;
    state.images = reset ? result.items : [...state.images, ...result.items];
    renderImages();
    renderChapterSummary();
    renderDaySummary();
    if (state.day === "unassigned") $("#dayImageCount").textContent = Number(state.total || 0);
    $("#pageTitle").textContent = activeChapter()?.title || activeDay()?.title || (state.day === "unassigned" ? "未归入 Day" : "抽帧图整理");
    if (state.day) $("#pageDescription").textContent = "浏览这一天的图片，并管理所属 Chapter。";
    else if (!activeChapter()) $("#pageDescription").textContent = "挑出值得保留的画面，再按 Day 和 Chapter 整理。";
  } catch (error) {
    if (listVersion !== state.listVersion) return;
    $("#imageGrid").innerHTML = `<div class="error-state">${escapeHtml(error.message)}</div>`;
    $("#emptyState").hidden = true;
  }
}

function renderSelectedCard(imageId) {
  const card = document.querySelector(`[data-image-card="${imageId}"]`);
  if (!card) return;
  const isChecked = state.selected.has(Number(imageId));
  card.classList.toggle("is-selected", isChecked);
  const checkbox = card.querySelector("input[type=checkbox]");
  if (checkbox) checkbox.checked = isChecked;
  renderSelectionBar();
}

function openImage(imageId) {
  const image = state.images.find((item) => item.image_id === Number(imageId));
  if (!image) return;
  state.activeImage = image;
  $("#previewImage").src = `/media/${image.image_id}`;
  $("#previewImage").alt = image.filename;
  $("#imageFilename").textContent = image.filename;
  $("#imageMeta").innerHTML = [`来源：${image.source}`, image.time_label ? `视频时间：${image.time_label}` : "", image.phase === null ? "" : `偏移为${image.offset_seconds}组`, image.frame_index === null ? "" : `帧序：${image.frame_index}`].filter(Boolean).map((label) => `<span class="meta-chip">${escapeHtml(label)}</span>`).join("");
  $("#imageStatus").value = image.status || "未处理";
  $("#imageTags").value = (image.tags || []).join("，");
  $("#imageNote").value = image.note || "";
  $("#imageChapterHint").textContent = image.chapter_count ? `已加入 ${image.chapter_count} 个 Chapter` : "还没有加入 Chapter";
  const chapter = activeChapter();
  const inCurrentChapter = Boolean(chapter && state.chapter && image.assigned);
  $("#imageAssignButton").textContent = state.chapter === "unassigned" ? "加入 Chapter" : inCurrentChapter ? "从当前 Chapter 移出" : "加入 Chapter";
  $("#imageAssignButton").dataset.remove = inCurrentChapter ? "1" : "0";
  $("#chapterTimelineHint").hidden = !chapter;
  $("#setCoverButton").hidden = !chapter;
  $("#setCoverButton").disabled = isImageGroupCover(chapter, image);
  $("#setCoverButton").textContent = isImageGroupCover(chapter, image) ? "本组当前封面" : "设为本组封面";
  $("#imageDialog").showModal();
}

function openChapterEditor(chapter = null) {
  state.chapterEditId = chapter?.chapter_id ?? null;
  $("#chapterDialogTitle").textContent = chapter ? "编辑 Chapter" : "新建 Chapter";
  $("#chapterNameInput").value = chapter?.title || "";
  $("#chapterDaySelect").innerHTML = `<option value="">未归入 Day</option>${state.days.map((day) => `<option value="${day.day_id}">${escapeHtml(day.title)}</option>`).join("")}`;
  $("#chapterDaySelect").value = String(chapter?.day_id || (state.day && state.day !== "unassigned" ? state.day : ""));
  $("#chapterTagsInput").value = (chapter?.tags || []).join("，");
  $("#chapterIntroInput").value = chapter?.intro || "";
  $("#deleteChapterButton").hidden = !chapter;
  $("#chapterDialog").showModal();
  $("#chapterNameInput").focus();
}

function openDayEditor(day = null) {
  state.dayEditId = day?.day_id ?? null;
  $("#dayDialogTitle").textContent = day ? "编辑 Day" : "新建 Day";
  $("#dayNameInput").value = day?.title || "";
  $("#dayIntroInput").value = day?.intro || "";
  $("#deleteDayButton").hidden = !day;
  $("#dayDialog").showModal();
  $("#dayNameInput").focus();
}

function openAssign(ids) {
  if (!state.chapters.length) {
    notify("请先新建一个 Chapter");
    openChapterEditor();
    return;
  }
  state.assignmentIds = [...ids].map(Number);
  $("#assignDescription").textContent = `将 ${state.assignmentIds.length} 张图片加入所选 Chapter。图片可以同时属于多个 Chapter。`;
  $("#assignChapterSelect").innerHTML = state.chapters.map((chapter) => `<option value="${chapter.chapter_id}">${escapeHtml(chapter.title)}</option>`).join("");
  if (activeChapter()) $("#assignChapterSelect").value = String(activeChapter().chapter_id);
  $("#assignDialog").showModal();
}

function closeDialog(id) {
  const dialog = document.getElementById(id);
  if (dialog?.open) dialog.close();
}

function openSceneGroupEditor(group = null) {
  if (!activeChapter()) return;
  state.sceneGroupEditId = group?.group_id ?? null;
  $("#sceneGroupDialogTitle").textContent = group ? "编辑风貌组" : "新建风貌组";
  $("#sceneGroupTitle").value = group?.title || "";
  $("#sceneGroupDescription").value = group?.description || "";
  $("#deleteSceneGroupButton").hidden = !group;
  $("#sceneGroupDialog").showModal();
  $("#sceneGroupTitle").focus();
}

async function saveSceneGroup(event) {
  event.preventDefault();
  const chapter = activeChapter();
  if (!chapter) return;
  const body = { title: $("#sceneGroupTitle").value.trim(), description: $("#sceneGroupDescription").value.trim() };
  setBusy(true);
  try {
    if (state.sceneGroupEditId) await api("/api/scene-groups/" + state.sceneGroupEditId, { method: "PATCH", body });
    else await api("/api/chapters/" + chapter.chapter_id + "/groups", { method: "POST", body });
    closeDialog("sceneGroupDialog");
    await refreshSceneGroups();
    renderImages();
    notify(state.sceneGroupEditId ? "风貌组已更新" : "风貌组已创建");
  } catch (error) { notify(error.message); }
  finally { setBusy(false); }
}

async function deleteSceneGroup() {
  if (!state.sceneGroupEditId || !window.confirm("删除这个风貌组？其中图片会回到未分组素材。")) return;
  setBusy(true);
  try {
    await api("/api/scene-groups/" + state.sceneGroupEditId, { method: "DELETE" });
    closeDialog("sceneGroupDialog");
    await refreshSceneGroups();
    renderImages();
    notify("风貌组已删除，图片仍在当前 Chapter");
  } catch (error) { notify(error.message); }
  finally { setBusy(false); }
}

function openSceneGroupAssign() {
  if (!activeChapter() || !state.selected.size) return;
  if (!state.sceneGroups.length) {
    notify("请先新建风貌组");
    openSceneGroupEditor();
    return;
  }
  $("#sceneGroupAssignDescription").textContent = "将 " + state.selected.size + " 张图片归入所选风貌组。已归组的图片会自动移入新组。";
  $("#sceneGroupSelect").innerHTML = state.sceneGroups.map((group) => '<option value="' + group.group_id + '">' + escapeHtml(group.title) + '</option>').join("");
  $("#sceneGroupAssignDialog").showModal();
}

async function assignSelectedToSceneGroup(event) {
  event.preventDefault();
  const groupId = $("#sceneGroupSelect").value;
  if (!groupId || !state.selected.size) return;
  const position = rememberGridPosition();
  setBusy(true);
  try {
    await api("/api/scene-groups/" + groupId + "/images", { method: "POST", body: { image_ids: [...state.selected] } });
    closeDialog("sceneGroupAssignDialog");
    await refreshSceneGroups();
    state.selected.clear();
    renderImages();
    restoreGridPosition(position);
    notify("图片已归入风貌组");
  } catch (error) { notify(error.message); }
  finally { setBusy(false); }
}

async function ungroupSelectedImages() {
  if (!activeChapter() || !state.selected.size) return;
  const position = rememberGridPosition();
  setBusy(true);
  try {
    for (const group of state.sceneGroups) {
      const ids = [...state.selected].filter((id) => group.image_ids.includes(id));
      if (ids.length) await api("/api/scene-groups/" + group.group_id + "/images", { method: "DELETE", body: { image_ids: ids } });
    }
    await refreshSceneGroups();
    state.selected.clear();
    renderImages();
    restoreGridPosition(position);
    notify("图片已移回未分组素材");
  } catch (error) { notify(error.message); }
  finally { setBusy(false); }
}

async function moveSceneGroup(groupId, direction) {
  const index = state.sceneGroups.findIndex((group) => group.group_id === groupId);
  const target = index + direction;
  if (index < 0 || target < 0 || target >= state.sceneGroups.length) return;
  const ordered = [...state.sceneGroups];
  [ordered[index], ordered[target]] = [ordered[target], ordered[index]];
  setBusy(true);
  try {
    await Promise.all(ordered.map((group, position) => api("/api/scene-groups/" + group.group_id, { method: "PATCH", body: { position: position + 1 } })));
    await refreshSceneGroups();
    renderImages();
  } catch (error) { notify(error.message); }
  finally { setBusy(false); }
}

function updateExportSummary() {
  const dayId = $("#exportDaySelect").value;
  const phase = $("#exportPhaseSelect").value;
  const day = state.days.find((item) => String(item.day_id) === dayId);
  const chapterCount = day
    ? state.chapters.filter((chapter) => chapter.day_id === day.day_id).length
    : state.chapters.length;
  const phaseItem = (state.meta.phases || []).find((item) => String(item.phase) === phase);
  const rangeLabel = day ? "「" + escapeHtml(day.title) + "」" : "全部 Day";
  const phaseLabel = phaseItem ? "偏移为" + phaseItem.offset_seconds + "组" : "全部偏移组";
  $("#exportSummary").innerHTML = "将导出 <strong>" + rangeLabel + "</strong> 的 " + chapterCount + " 个 Chapter；图片范围为 <strong>" + phaseLabel + "</strong>。";
}

function openExport(dayId = "") {
  const currentChapter = activeChapter();
  const defaultDayId = dayId || String(activeDay()?.day_id || currentChapter?.day_id || "");
  $("#exportDaySelect").innerHTML = '<option value="">全部 Day</option>' + state.days.map((day) => '<option value="' + day.day_id + '">' + escapeHtml(day.title) + "</option>").join("");
  $("#exportDaySelect").value = defaultDayId;
  const phases = state.meta.phases || [];
  $("#exportPhaseSelect").innerHTML = '<option value="">全部偏移组</option>' + phases.map((item) => '<option value="' + item.phase + '">偏移为' + item.offset_seconds + "组</option>").join("");
  $("#exportPhaseSelect").value = state.phase || String(phases[0]?.phase ?? "");
  updateExportSummary();
  $("#exportDialog").showModal();
}

async function exportResults(event) {
  event.preventDefault();
  const dayId = $("#exportDaySelect").value;
  const phase = $("#exportPhaseSelect").value;
  const submitButton = $("#exportSubmitButton");
  submitButton.disabled = true;
  setBusy(true, "正在生成导出文件夹");
  try {
    const result = await api("/api/export-folder", {
      method: "POST",
      body: {
        day: dayId ? Number(dayId) : null,
        phase: phase === "" ? null : Number(phase),
      },
    });
    $("#exportSummary").innerHTML = `<strong>生成完成</strong><br>共 ${result.chapter_count} 个 Chapter、${result.image_count} 张图片。<code>${escapeHtml(result.path)}</code>`;
    notify("导出文件夹已生成");
  } catch (error) {
    notify(error.message);
  } finally {
    submitButton.disabled = false;
    setBusy(false);
  }
}

async function saveImage() {
  if (!state.activeImage) return;
  setBusy(true);
  try {
    await api(`/api/images/${state.activeImage.image_id}`, {
      method: "PATCH",
      body: { status: $("#imageStatus").value, tags: splitTags($("#imageTags").value), note: $("#imageNote").value },
    });
    closeDialog("imageDialog");
    notify("图片标注已保存");
    await Promise.all([refreshVisibleImages(), refreshMeta()]);
  } catch (error) {
    notify(error.message);
  } finally { setBusy(false); }
}

async function saveChapter(event) {
  event.preventDefault();
  const body = { title: $("#chapterNameInput").value, tags: splitTags($("#chapterTagsInput").value), intro: $("#chapterIntroInput").value, day_id: $("#chapterDaySelect").value || null };
  setBusy(true);
  try {
    if (state.chapterEditId) {
      await api(`/api/chapters/${state.chapterEditId}`, { method: "PATCH", body });
      state.chapter = String(state.chapterEditId);
      state.day = "";
      notify("Chapter 信息已更新");
    } else {
      const result = await api("/api/chapters", { method: "POST", body });
      state.chapter = String(result.chapter_id);
      state.day = "";
      state.source = "";
      state.status = "";
      notify("Chapter 已创建，可以开始加入图片");
    }
    closeDialog("chapterDialog");
    await Promise.all([refreshHierarchy(), refreshMeta()]);
    await loadImages();
  } catch (error) {
    notify(error.message);
  } finally { setBusy(false); }
}

async function saveDay(event) {
  event.preventDefault();
  const body = { title: $("#dayNameInput").value.trim(), intro: $("#dayIntroInput").value };
  setBusy(true);
  try {
    const result = state.dayEditId
      ? await api(`/api/days/${state.dayEditId}`, { method: "PATCH", body })
      : await api("/api/days", { method: "POST", body });
    state.day = String(result.day_id);
    state.chapter = "";
    state.source = "";
    closeDialog("dayDialog");
    await refreshHierarchy();
    await loadImages();
    notify(state.dayEditId ? "Day 信息已更新" : "Day 已创建，可将 Chapter 归入这一天");
  } catch (error) { notify(error.message); }
  finally { setBusy(false); }
}

async function assignImages() {
  const chapterId = $("#assignChapterSelect").value;
  if (!chapterId || !state.assignmentIds.length) return;
  const position = rememberGridPosition();
  setBusy(true);
  try {
    await api(`/api/chapters/${chapterId}/images`, { method: "POST", body: { image_ids: state.assignmentIds } });
    state.selected.clear();
    closeDialog("assignDialog");
    notify(`已将图片加入「${state.chapters.find((item) => String(item.chapter_id) === chapterId)?.title || "Chapter"}」`);
    await Promise.all([refreshHierarchy(), refreshMeta()]);
    await refreshVisibleImages(position);
  } catch (error) {
    notify(error.message);
  } finally { setBusy(false); }
}

async function removeFromCurrentChapter(imageId) {
  const chapter = activeChapter();
  if (!chapter) return;
  setBusy(true);
  try {
    await api(`/api/chapters/${chapter.chapter_id}/images`, { method: "DELETE", body: { image_ids: [imageId] } });
    closeDialog("imageDialog");
    notify("已从当前 Chapter 移出");
    await Promise.all([refreshHierarchy(), refreshMeta()]);
    await refreshVisibleImages();
  } catch (error) { notify(error.message); }
  finally { setBusy(false); }
}

async function removeSelectedFromCurrentChapter() {
  const chapter = activeChapter();
  const imageIds = [...state.selected];
  if (!chapter || !imageIds.length) return;
  const position = rememberGridPosition();
  setBusy(true);
  try {
    await api(`/api/chapters/${chapter.chapter_id}/images`, { method: "DELETE", body: { image_ids: imageIds } });
    state.selected.clear();
    notify(`已从「${chapter.title}」移出 ${imageIds.length} 张图片`);
    await Promise.all([refreshHierarchy(), refreshMeta()]);
    await refreshVisibleImages(position);
  } catch (error) { notify(error.message); }
  finally { setBusy(false); }
}

async function setCover(imageId = state.activeImage?.image_id) {
  const chapter = activeChapter();
  const image = state.images.find((item) => item.image_id === Number(imageId)) || state.activeImage;
  if (!chapter || !imageId || !image || image.phase === null) return;
  setBusy(true);
  try {
    await api(`/api/chapters/${chapter.chapter_id}`, { method: "PATCH", body: { title: chapter.title, intro: chapter.intro, tags: chapter.tags, cover_image_id: Number(imageId), cover_phase: image.phase } });
    notify(`已设为偏移为${image.offset_seconds}组的 Chapter 封面`);
    await refreshChapters();
    renderImages();
    renderChapterSummary();
    renderDaySummary();
    if ($("#imageDialog").open) {
      $("#setCoverButton").disabled = true;
      $("#setCoverButton").textContent = "本组当前封面";
    }
  } catch (error) { notify(error.message); }
  finally { setBusy(false); }
}

async function updateBulkStatus() {
  const status = $("#bulkStatusSelect").value;
  if (!status || !state.selected.size) return;
  setBusy(true);
  try {
    await api("/api/images/bulk", { method: "PATCH", body: { image_ids: [...state.selected], status } });
    state.selected.clear();
    $("#bulkStatusSelect").value = "";
    notify(`已更新为「${status}」`);
    await Promise.all([refreshVisibleImages(), refreshMeta()]);
  } catch (error) { notify(error.message); }
  finally { setBusy(false); }
}

async function scanImages() {
  const button = $("#scanButton");
  button.disabled = true;
  button.textContent = "正在扫描…";
  try {
    const result = await api("/api/scan", { method: "POST", body: {} });
    await Promise.all([refreshMeta(), refreshHierarchy(), loadImages()]);
    notify(`扫描完成，共找到 ${Number(result.image_count).toLocaleString("zh-CN")} 张图片`);
  } catch (error) { notify(error.message); }
  finally { button.disabled = false; button.textContent = "重新扫描"; }
}

function importRelativePath(file) {
  return String(file.webkitRelativePath || file.name).split(/[\\/]+/).filter(Boolean).join("/");
}

async function uploadImportFile(file, relativePath = importRelativePath(file)) {
  const response = await fetch(`/api/import-file?path=${encodeURIComponent(relativePath)}`, {
    method: "POST",
    headers: { "Content-Type": "application/octet-stream" },
    body: file,
  });
  const payload = await response.json().catch(() => null);
  if (!response.ok) throw new Error(payload?.error || `导入失败（${response.status}）`);
  return payload;
}

async function buildStructuredImport(allFiles, manifestFile, folderName) {
  let manifest;
  try {
    manifest = JSON.parse(await manifestFile.text());
  } catch (error) {
    throw new Error(`整理结果.json 无法读取：${error.message}`);
  }
  if (!Array.isArray(manifest.days) || !Array.isArray(manifest.chapters)) {
    throw new Error("整理结果.json 缺少 Day 或 Chapter 数据");
  }

  const physicalImages = new Map();
  for (const file of allFiles) {
    if (!/\.(?:jpe?g|png|webp)$/i.test(file.name)) continue;
    const parts = importRelativePath(file).split("/");
    const dayIndex = parts.findIndex((part) => /^\d+_(?:Day_|未归入Day)/.test(part));
    const imageFolderIndex = parts.lastIndexOf("图片");
    if (dayIndex < 0 || imageFolderIndex < 0 || imageFolderIndex <= dayIndex + 1) continue;
    const dayMatch = parts[dayIndex].match(/^(\d+)_/);
    const chapterMatch = parts[dayIndex + 1].match(/^(\d+)_/);
    const imageMatch = parts[imageFolderIndex + 1]?.match(/^(\d+)_/);
    if (dayMatch && chapterMatch && imageMatch) {
      physicalImages.set(`${Number(dayMatch[1])}:${Number(chapterMatch[1])}:${Number(imageMatch[1])}`, file);
    }
  }

  const uploads = new Map();
  const structure = {
    package_key: `${folderName}|${manifest.exported_at || "未记录导出时间"}`,
    days: manifest.days.map((day, index) => ({
      external_id: String(day.day_id ?? index + 1),
      title: day.title,
      intro: day.intro || "",
    })),
    chapters: [],
  };
  const missing = [];

  manifest.days.forEach((day, dayIndex) => {
    const dayChapters = manifest.chapters.filter((chapter) => String(chapter.day_id) === String(day.day_id));
    dayChapters.forEach((chapter, chapterIndex) => {
      const importedImages = (chapter.images || []).map((image, imageIndex) => {
        const file = physicalImages.get(`${dayIndex + 1}:${chapterIndex + 1}:${imageIndex + 1}`);
        if (!file) {
          missing.push(`${day.title} / ${chapter.title} / 图片 ${imageIndex + 1}`);
          return null;
        }
        const filename = String(image.filename || file.name).split(/[\\/]+/).pop();
        const importPath = `${folderName}/${filename}`;
        if (!uploads.has(importPath)) uploads.set(importPath, { file, importPath });
        return {
          import_path: importPath,
          original_rel_path: image.rel_path || "",
          status: image.status || "未处理",
          tags: image.tags || [],
          note: image.note || "",
        };
      }).filter(Boolean);
      const coverImage = (chapter.images || []).find((image) => image.rel_path === chapter.cover_image_path);
      const coverFilename = coverImage ? String(coverImage.filename || "").split(/[\\/]+/).pop() : "";
      structure.chapters.push({
        external_id: String(chapter.chapter_id ?? `${dayIndex + 1}-${chapterIndex + 1}`),
        external_day_id: String(day.day_id ?? dayIndex + 1),
        title: chapter.title,
        intro: chapter.intro || "",
        tags: chapter.tags || [],
        cover_phase: manifest.filters?.phase,
        cover_import_path: coverFilename ? `${folderName}/${coverFilename}` : null,
        images: importedImages,
      });
    });
  });

  if (missing.length) {
    throw new Error(`整理结果包缺少 ${missing.length} 张图片：${missing.slice(0, 3).join("；")}`);
  }
  return { uploads: [...uploads.values()], structure };
}

function renderImportProgress(summary, total, currentFile = "") {
  $("#importProgress").max = Math.max(total, 1);
  $("#importProgress").value = summary.completed;
  $("#importCompleted").textContent = summary.completed.toLocaleString("zh-CN");
  $("#importAdded").textContent = summary.imported.toLocaleString("zh-CN");
  $("#importSkipped").textContent = summary.skipped.toLocaleString("zh-CN");
  $("#importFailed").textContent = summary.failed.length.toLocaleString("zh-CN");
  $("#importStatus").textContent = currentFile
    ? `正在处理 ${summary.completed + 1} / ${total}：${currentFile}`
    : `已处理 ${summary.completed.toLocaleString("zh-CN")} / ${total.toLocaleString("zh-CN")} 张图片`;
}

async function importFolder(event) {
  const input = event.currentTarget;
  const supported = /\.(?:jpe?g|png|webp)$/i;
  const allFiles = [...input.files];
  const imageFiles = allFiles.filter((file) => supported.test(file.name));
  if (!imageFiles.length) {
    notify("所选文件夹中没有可导入的 JPG、PNG 或 WebP 图片");
    input.value = "";
    return;
  }

  const folderName = importRelativePath(allFiles[0]).split("/")[0] || "新导入素材";
  const manifestFile = allFiles.find((file) => file.name === "整理结果.json" && importRelativePath(file).split("/").length === 2);
  let prepared;
  try {
    prepared = manifestFile
      ? await buildStructuredImport(allFiles, manifestFile, folderName)
      : { uploads: imageFiles.map((file) => ({ file, importPath: importRelativePath(file) })), structure: null };
  } catch (error) {
    notify(error.message);
    input.value = "";
    return;
  }
  const files = prepared.uploads;
  const summary = { completed: 0, imported: 0, skipped: 0, failed: [] };
  const importButton = $("#importButton");
  const closeButton = $("#importDialogClose");
  const doneButton = $("#importDoneButton");
  state.importing = true;
  importButton.disabled = true;
  importButton.textContent = "正在导入…";
  closeButton.disabled = true;
  doneButton.disabled = true;
  $("#importFolderName").textContent = prepared.structure
    ? `已识别整理结果包「${folderName}」，将恢复 Day、Chapter、分组封面和图片备注。`
    : `「${folderName}」将作为新的图片来源；同路径图片会自动跳过。`;
  renderImportProgress(summary, files.length, files[0].file.name);
  if (!$("#importDialog").open) $("#importDialog").showModal();
  setBusy(true, "正在导入图片");

  let cursor = 0;
  const worker = async () => {
    while (cursor < files.length) {
      const item = files[cursor++];
      renderImportProgress(summary, files.length, item.file.name);
      try {
        const result = await uploadImportFile(item.file, item.importPath);
        if (result.status === "imported") summary.imported += 1;
        else summary.skipped += 1;
      } catch (error) {
        summary.failed.push(`${item.file.name}：${error.message}`);
      } finally {
        summary.completed += 1;
        renderImportProgress(summary, files.length);
      }
    }
  };

  try {
    await Promise.all(Array.from({ length: Math.min(4, files.length) }, worker));
    const result = await api("/api/import-complete", { method: "POST", body: {} });
    const restored = prepared.structure
      ? await api("/api/import-structure", { method: "POST", body: prepared.structure })
      : null;
    state.source = restored ? "" : folderName;
    state.day = restored?.day_ids?.[0] ? String(restored.day_ids[0]) : "";
    state.chapter = "";
    state.selected.clear();
    await Promise.all([refreshMeta(), refreshHierarchy()]);
    await loadImages();
    const failureText = summary.failed.length ? `，${summary.failed.length} 张失败：${summary.failed.slice(0, 2).join("；")}` : "";
    const restoredText = restored ? `，恢复 ${restored.day_count} 个 Day、${restored.chapter_count} 个 Chapter` : "";
    $("#importStatus").textContent = `导入完成：新增 ${summary.imported} 张，跳过 ${summary.skipped} 张${restoredText}。图片库现有 ${Number(result.image_count).toLocaleString("zh-CN")} 张图片${failureText}`;
    notify(restored ? `已恢复「${folderName}」的完整整理结果` : `已导入「${folderName}」：新增 ${summary.imported} 张图片`);
  } catch (error) {
    $("#importStatus").textContent = `图片已复制，但刷新索引失败：${error.message}`;
    notify(error.message);
  } finally {
    state.importing = false;
    importButton.disabled = false;
    importButton.textContent = "导入文件夹";
    closeButton.disabled = false;
    doneButton.disabled = false;
    input.value = "";
    setBusy(false);
  }
}

function wireEvents() {
  $("#sourceNav").addEventListener("click", (event) => {
    const button = event.target.closest("[data-source]");
    if (!button) return;
    state.source = button.dataset.source;
    state.chapter = "";
    state.day = "";
    renderSidebar();
    loadImages();
  });
  function handleHierarchyClick(event) {
    const editDay = event.target.closest("[data-edit-day]");
    if (editDay) {
      const day = state.days.find((item) => item.day_id === Number(editDay.dataset.editDay));
      if (day) openDayEditor(day);
      return;
    }
    const editTarget = event.target.closest("[data-edit-chapter]");
    if (editTarget) {
      const chapter = state.chapters.find((item) => item.chapter_id === Number(editTarget.dataset.editChapter));
      if (chapter) openChapterEditor(chapter);
      return;
    }
    const button = event.target.closest("[data-chapter]");
    if (button) {
      state.chapter = button.dataset.chapter;
      state.day = "";
      state.source = "";
      renderSidebar();
      loadImages();
      return;
    }
    const dayButton = event.target.closest("[data-day]");
    if (dayButton) {
      state.day = dayButton.dataset.day;
      state.chapter = "";
      state.source = "";
      renderSidebar();
      loadImages();
    }
  }
  $("#dayNav").addEventListener("click", handleHierarchyClick);
  $("#chapterNav").addEventListener("click", handleHierarchyClick);
  $("#orphanDayButton").addEventListener("click", () => {
    state.day = "unassigned";
    state.chapter = "";
    state.source = "";
    renderSidebar();
    loadImages();
  });
  $("#unassignedButton").addEventListener("click", () => {
    state.chapter = "unassigned";
    state.day = "";
    state.source = "";
    renderSidebar();
    loadImages();
  });
  $("#imageGrid").addEventListener("change", (event) => {
    const checkbox = event.target.closest("[data-select-image]");
    if (!checkbox) return;
    const id = Number(checkbox.dataset.selectImage);
    if (checkbox.checked) state.selected.add(id); else state.selected.delete(id);
    renderSelectedCard(id);
  });
  $("#imageGrid").addEventListener("click", async (event) => {
    const editGroup = event.target.closest("[data-edit-scene-group]");
    if (editGroup) {
      const group = state.sceneGroups.find((item) => item.group_id === Number(editGroup.dataset.editSceneGroup));
      if (group) openSceneGroupEditor(group);
      return;
    }
    const moveGroup = event.target.closest("[data-move-scene-group]");
    if (moveGroup) {
      await moveSceneGroup(Number(moveGroup.dataset.moveSceneGroup), Number(moveGroup.dataset.direction));
      return;
    }
    const finalButton = event.target.closest("[data-toggle-final]");
    if (finalButton) {
      event.stopPropagation();
      const imageId = Number(finalButton.dataset.toggleFinal);
      const image = state.images.find((item) => item.image_id === imageId);
      if (!image) return;
      const status = image.status === "终选" ? "候选" : "终选";
      try {
        setBusy(true);
        await api(`/api/images/${imageId}`, { method: "PATCH", body: { status, tags: image.tags || [], note: image.note || "" } });
        image.status = status;
        renderImages();
        notify(status === "终选" ? "已加入终选" : "已取消终选，移回候选");
      } catch (error) { notify(error.message); }
      finally { setBusy(false); }
      return;
    }
    const target = event.target.closest("[data-open-image]");
    if (target) openImage(target.dataset.openImage);
  });
  $("#searchInput").addEventListener("input", () => {
    clearTimeout(state.searchTimer);
    state.searchTimer = setTimeout(() => { state.query = $("#searchInput").value.trim(); loadImages(); }, 220);
  });
  $("#statusFilter").addEventListener("change", () => { state.status = $("#statusFilter").value; loadImages(); });
  $("#phaseFilter").addEventListener("change", () => { state.phase = $("#phaseFilter").value; loadImages(); });
  $("#loadMoreButton").addEventListener("click", async () => {
    const button = $("#loadMoreButton");
    if (!state.hasMore || button.disabled) return;
    const nextPage = state.page + 1;
    const listVersion = state.listVersion;
    button.disabled = true;
    button.textContent = "正在加载…";
    try {
      const result = await api(`/api/images?${imageParams(nextPage, PAGE_SIZE)}`);
      if (listVersion !== state.listVersion) return;
      state.page = nextPage;
      state.total = result.total;
      state.hasMore = result.has_more;
      state.images = [...state.images, ...result.items];
      renderImages(true, result.items);
    } catch (error) { notify(error.message); }
    finally {
      button.disabled = false;
      button.textContent = "加载更多图片";
    }
  });
  $("#clearSelectionButton").addEventListener("click", () => { state.selected.clear(); renderImages(); });
  $("#bulkStatusSelect").addEventListener("change", updateBulkStatus);
  $("#assignButton").addEventListener("click", () => openAssign(state.selected));
  $("#assignGroupButton").addEventListener("click", openSceneGroupAssign);
  $("#ungroupSelectedButton").addEventListener("click", ungroupSelectedImages);
  $("#removeSelectedButton").addEventListener("click", removeSelectedFromCurrentChapter);
  $("#createChapterButton").addEventListener("click", () => openChapterEditor());
  $("#newChapterButton").addEventListener("click", () => openChapterEditor());
  $("#createDayButton").addEventListener("click", () => openDayEditor());
  $("#newDayButton").addEventListener("click", () => openDayEditor());
  $("#editDayButton").addEventListener("click", () => { const day = activeDay(); if (day) openDayEditor(day); });
  $("#exportDayButton").addEventListener("click", () => { const day = activeDay(); if (day) openExport(String(day.day_id)); });
  $("#dayChapters").addEventListener("click", (event) => {
    const button = event.target.closest("[data-summary-chapter]");
    if (!button) return;
    state.chapter = button.dataset.summaryChapter;
    state.day = "";
    renderSidebar();
    loadImages();
  });
  $("#editChapterButton").addEventListener("click", () => { const chapter = activeChapter(); if (chapter) openChapterEditor(chapter); });
  $("#addSceneGroupButton").addEventListener("click", () => openSceneGroupEditor());
  $("#sceneGroupForm").addEventListener("submit", saveSceneGroup);
  $("#sceneGroupAssignForm").addEventListener("submit", assignSelectedToSceneGroup);
  $("#deleteSceneGroupButton").addEventListener("click", deleteSceneGroup);
  $("#chapterForm").addEventListener("submit", saveChapter);
  $("#dayForm").addEventListener("submit", saveDay);
  $("#imageForm").addEventListener("submit", (event) => { event.preventDefault(); saveImage(); });
  $("#assignForm").addEventListener("submit", (event) => { event.preventDefault(); assignImages(); });
  $("#exportForm").addEventListener("submit", exportResults);
  $("#exportDaySelect").addEventListener("change", updateExportSummary);
  $("#exportPhaseSelect").addEventListener("change", updateExportSummary);
  $("#imageAssignButton").addEventListener("click", () => {
    if (!state.activeImage) return;
    if ($("#imageAssignButton").dataset.remove === "1") removeFromCurrentChapter(state.activeImage.image_id);
    else openAssign([state.activeImage.image_id]);
  });
  $("#setCoverButton").addEventListener("click", () => setCover());
  $("#imageGrid").addEventListener("click", (event) => {
    const button = event.target.closest("[data-set-cover]");
    if (!button) return;
    event.stopPropagation();
    setCover(button.dataset.setCover);
  });
  $("#importButton").addEventListener("click", () => {
    if (state.importing) return;
    $("#importFolderInput").value = "";
    $("#importFolderInput").click();
  });
  $("#importFolderInput").addEventListener("change", importFolder);
  $("#scanButton").addEventListener("click", scanImages);
  $("#exportButton").addEventListener("click", () => openExport());
  $("#deleteChapterButton").addEventListener("click", async () => {
    if (!state.chapterEditId) return;
    if (!window.confirm("删除这个 Chapter？图片文件和图片标注会保留。")) return;
    try {
      await api(`/api/chapters/${state.chapterEditId}`, { method: "DELETE" });
      state.chapter = "";
      closeDialog("chapterDialog");
      await Promise.all([refreshHierarchy(), refreshMeta(), loadImages()]);
      notify("Chapter 已删除");
    } catch (error) { notify(error.message); }
  });
  $("#deleteDayButton").addEventListener("click", async () => {
    if (!state.dayEditId) return;
    if (!window.confirm("删除这个 Day？其中的 Chapter 会保留，并移到「未归入 Day」。")) return;
    try {
      await api(`/api/days/${state.dayEditId}`, { method: "DELETE" });
      state.day = "unassigned";
      state.chapter = "";
      closeDialog("dayDialog");
      await refreshHierarchy();
      await loadImages();
      notify("Day 已删除，Chapter 已移到「未归入 Day」");
    } catch (error) { notify(error.message); }
  });
  document.addEventListener("click", (event) => {
    const close = event.target.closest("[data-close]");
    if (close) closeDialog(close.dataset.close);
  });
  for (const dialog of document.querySelectorAll("dialog")) {
    dialog.addEventListener("cancel", (event) => {
      if (dialog.id === "importDialog" && state.importing) event.preventDefault();
    });
    dialog.addEventListener("click", (event) => {
      if (event.target === dialog && !(dialog.id === "importDialog" && state.importing)) dialog.close();
    });
  }
}

async function start() {
  wireEvents();
  try {
    await Promise.all([refreshMeta(), refreshHierarchy()]);
    await loadImages();
  } catch (error) {
    $("#imageGrid").innerHTML = `<div class="error-state">无法连接本地图片库：${escapeHtml(error.message)}</div>`;
  }
}

start();
