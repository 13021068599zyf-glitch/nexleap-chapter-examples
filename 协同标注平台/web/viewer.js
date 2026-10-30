const viewerState = {
  meta: { phases: [], image_count: 0 },
  days: [],
  chapters: [],
  dayId: null,
  phase: null,
  chapterId: null,
  dayImages: [],
  chapterEntries: [],
};

const $ = (selector) => document.querySelector(selector);
const escapeHtml = (value) => String(value ?? "").replace(/[&<>"']/g, (character) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[character]);

async function api(path) {
  const response = await fetch(path);
  const payload = await response.json();
  if (!response.ok) throw new Error(payload?.error || `读取失败（${response.status}）`);
  return payload;
}

function phaseInfo(phase = viewerState.phase) {
  return viewerState.meta.phases.find((item) => Number(item.phase) === Number(phase)) || { phase, offset_seconds: Number(phase || 0) * 10 };
}

function phaseLabel(phase = viewerState.phase) {
  return `偏移为${phaseInfo(phase).offset_seconds}组`;
}

function chapterCover(chapter) {
  return chapter?.covers?.[String(viewerState.phase)] || null;
}

function parseInitialState() {
  const query = new URLSearchParams(window.location.search);
  const day = Number(query.get("day"));
  const phase = Number(query.get("phase"));
  const chapter = Number(query.get("chapter"));
  if (Number.isInteger(day) && day > 0) viewerState.dayId = day;
  if (query.has("phase") && Number.isInteger(phase) && phase >= 0) viewerState.phase = phase;
  if (Number.isInteger(chapter) && chapter > 0) viewerState.chapterId = chapter;
}

function updateAddress() {
  const query = new URLSearchParams();
  if (viewerState.dayId) query.set("day", viewerState.dayId);
  if (viewerState.phase !== null) query.set("phase", viewerState.phase);
  if (viewerState.chapterId) query.set("chapter", viewerState.chapterId);
  history.replaceState(null, "", `/chapters?${query}`);
}

function renderDaySwitcher() {
  $("#daySwitcher").innerHTML = viewerState.days.map((day) => `
    <button class="day-tab ${day.day_id === viewerState.dayId ? "active" : ""}" type="button" data-day="${day.day_id}">
      ${escapeHtml(day.title)}
    </button>`).join("");
}

function renderPhaseSelect(phases) {
  $("#phaseSelect").innerHTML = phases.map((phase) => `<option value="${phase}">${escapeHtml(phaseLabel(phase))}</option>`).join("");
  $("#phaseSelect").value = String(viewerState.phase);
}

function renderDayHero() {
  const day = viewerState.days.find((item) => item.day_id === viewerState.dayId);
  const activeEntry = viewerState.chapterEntries.find((entry) => entry.chapter_id === viewerState.chapterId);
  const visualEntry = activeEntry || viewerState.chapterEntries.find((entry) => chapterCover(entry));
  const cover = chapterCover(visualEntry);
  $("#dayTitle").textContent = day?.title || "未命名旅程";
  $("#dayIntro").textContent = day?.intro || "这一天还没有填写简介。";
  $("#dayChapterCount").textContent = viewerState.chapterEntries.filter((entry) => entry.images.length).length;
  $("#dayImageCount").textContent = viewerState.dayImages.filter((image) => image.phase === viewerState.phase).length;
  $("#dayGroupValue").textContent = `${phaseInfo().offset_seconds}s`;
  $("#heroGroupBadge").textContent = phaseLabel();
  const heroImage = $("#dayHeroImage");
  heroImage.hidden = !cover;
  if (cover) {
    heroImage.src = `/media/${cover.image_id}`;
    heroImage.alt = `${day?.title || "Day"}封面`;
  }
}

function renderChapterRoute() {
  $("#chapterRoute").innerHTML = viewerState.chapterEntries.map((chapter, index) => {
    const cover = chapterCover(chapter);
    const time = chapter.images[0]?.time_label || "本组暂无画面";
    return `<button class="route-item ${chapter.chapter_id === viewerState.chapterId ? "active" : ""} ${chapter.images.length ? "" : "no-images"}" type="button" data-chapter="${chapter.chapter_id}">
      <span class="route-thumb">${cover ? `<img src="/media/${cover.image_id}" alt="">` : "暂无"}</span>
      <span class="route-copy"><small>${String(index + 1).padStart(2, "0")} · ${escapeHtml(time)}</small><strong>${escapeHtml(chapter.title)}</strong><em>${chapter.images.length} 张画面</em></span>
    </button>`;
  }).join("") || `<div class="viewer-empty"><span>○</span><h3>这个 Day 还没有 Chapter</h3></div>`;
}

function renderChapter() {
  const chapter = viewerState.chapterEntries.find((entry) => entry.chapter_id === viewerState.chapterId);
  if (!chapter) {
    $("#chapterTitle").textContent = "暂无 Chapter";
    $("#imageGallery").innerHTML = "";
    $("#viewerEmpty").hidden = false;
    renderDayHero();
    return;
  }
  const sequence = viewerState.chapterEntries.indexOf(chapter) + 1;
  const cover = chapterCover(chapter);
  $("#chapterSequence").textContent = `CHAPTER ${String(sequence).padStart(2, "0")} / ${phaseLabel()}`;
  $("#chapterTitle").textContent = chapter.title;
  $("#chapterIntro").textContent = chapter.intro || "这个 Chapter 还没有填写简介。";
  $("#chapterTags").innerHTML = (chapter.tags || []).map((tag) => `<span>${escapeHtml(tag)}</span>`).join("");
  $("#chapterImageTotal").textContent = `${chapter.images.length} 张`;
  $("#chapterCoverLabel").textContent = `CHAPTER COVER · ${phaseLabel()}`;
  $("#chapterCover").hidden = !cover;
  $("#chapterCoverEmpty").hidden = Boolean(cover);
  if (cover) {
    $("#chapterCover").src = `/media/${cover.image_id}`;
    $("#chapterCover").alt = `${chapter.title}封面`;
  }
  renderGallery(chapter);
  renderChapterRoute();
  renderDayHero();
  updateAddress();
}

function renderGallery(chapter) {
  const images = chapter.images;
  $("#viewerEmpty").hidden = images.length > 0;
  $("#imageGallery").innerHTML = images.map((image, index) => `
    <figure class="image-entry" data-image-id="${image.image_id}">
      <button class="image-entry-button" type="button" data-open-image="${image.image_id}" aria-label="查看第 ${index + 1} 张图片">
        <img src="/media/${image.image_id}" alt="${escapeHtml(image.note || image.filename)}" loading="lazy">
      </button>
      <figcaption>
        <span class="image-index">${String(index + 1).padStart(2, "0")}</span>
        <span class="image-caption-copy"><span class="image-time">${escapeHtml(image.time_label || "时间未知")}</span><p class="image-note">${escapeHtml(image.note || "暂无备注。")}</p></span>
      </figcaption>
    </figure>`).join("");
}

async function loadDay(dayId) {
  viewerState.dayId = Number(dayId);
  viewerState.chapterId = null;
  renderDaySwitcher();
  $("#imageGallery").innerHTML = `<div class="loading-block">正在沿时间整理画面…</div>`;
  const dayPayload = await api(`/api/images?day=${viewerState.dayId}&page=1&limit=5000`);
  viewerState.dayImages = dayPayload.items;
  const availablePhases = [...new Set(viewerState.dayImages.map((image) => image.phase).filter((phase) => phase !== null))].sort((a, b) => a - b);
  if (!availablePhases.includes(viewerState.phase)) viewerState.phase = availablePhases[0] ?? viewerState.meta.phases[0]?.phase ?? 0;
  renderPhaseSelect(availablePhases.length ? availablePhases : viewerState.meta.phases.map((item) => item.phase));
  await loadChapterEntries();
}

async function loadChapterEntries() {
  const chapters = viewerState.chapters.filter((chapter) => chapter.day_id === viewerState.dayId);
  const entries = await Promise.all(chapters.map(async (chapter) => {
    const payload = await api(`/api/images?chapter=${chapter.chapter_id}&phase=${viewerState.phase}&page=1&limit=5000`);
    return { ...chapter, images: payload.items };
  }));
  entries.sort((left, right) => {
    const positionDifference = Number(left.position || 99999) - Number(right.position || 99999);
    const leftTime = left.images[0]?.time_label || "99:99:99";
    const rightTime = right.images[0]?.time_label || "99:99:99";
    return positionDifference || leftTime.localeCompare(rightTime) || left.chapter_id - right.chapter_id;
  });
  viewerState.chapterEntries = entries;
  const requested = entries.find((entry) => entry.chapter_id === viewerState.chapterId);
  viewerState.chapterId = requested?.chapter_id || entries.find((entry) => entry.images.length)?.chapter_id || entries[0]?.chapter_id || null;
  renderChapter();
}

function openImage(imageId) {
  const chapter = viewerState.chapterEntries.find((entry) => entry.chapter_id === viewerState.chapterId);
  const image = chapter?.images.find((item) => item.image_id === Number(imageId));
  if (!image) return;
  $("#dialogImage").src = `/media/${image.image_id}`;
  $("#dialogImage").alt = image.note || image.filename;
  $("#dialogMeta").textContent = `${phaseLabel(image.phase)} · ${image.time_label || "时间未知"}`;
  $("#dialogTitle").textContent = chapter.title;
  $("#dialogNote").textContent = image.note || "暂无备注。";
  $("#imageDialog").showModal();
}

function wireEvents() {
  $("#daySwitcher").addEventListener("click", (event) => {
    const button = event.target.closest("[data-day]");
    if (button) loadDay(button.dataset.day).catch(showError);
  });
  $("#phaseSelect").addEventListener("change", () => {
    viewerState.phase = Number($("#phaseSelect").value);
    viewerState.chapterId = null;
    loadChapterEntries().catch(showError);
  });
  $("#chapterRoute").addEventListener("click", (event) => {
    const button = event.target.closest("[data-chapter]");
    if (!button) return;
    viewerState.chapterId = Number(button.dataset.chapter);
    renderChapter();
    $("#chapterHeader").scrollIntoView({ behavior: "smooth", block: "start" });
  });
  $("#imageGallery").addEventListener("click", (event) => {
    const button = event.target.closest("[data-open-image]");
    if (button) openImage(button.dataset.openImage);
  });
  $("#dialogClose").addEventListener("click", () => $("#imageDialog").close());
  $("#imageDialog").addEventListener("click", (event) => {
    if (event.target === $("#imageDialog")) $("#imageDialog").close();
  });
}

function showError(error) {
  $("#footerStatus").textContent = error.message;
  $("#imageGallery").innerHTML = `<div class="viewer-empty"><span>!</span><h3>数据库读取失败</h3><p>${escapeHtml(error.message)}</p></div>`;
}

async function initializeViewer() {
  parseInitialState();
  wireEvents();
  try {
    [viewerState.meta, viewerState.days, viewerState.chapters] = await Promise.all([
      api("/api/meta"), api("/api/days"), api("/api/chapters"),
    ]);
    $("#archiveCount").textContent = `${viewerState.days.length} 个 Day · ${viewerState.chapters.length} 个 Chapter · ${viewerState.meta.image_count} 张原图`;
    const requestedDay = viewerState.days.find((day) => day.day_id === viewerState.dayId);
    viewerState.dayId = requestedDay?.day_id || viewerState.days[0]?.day_id || null;
    renderDaySwitcher();
    if (viewerState.dayId) await loadDay(viewerState.dayId);
    else showError(new Error("数据库中还没有 Day"));
    $("#footerStatus").textContent = `最后读取：${new Date().toLocaleTimeString("zh-CN", { hour: "2-digit", minute: "2-digit" })}`;
  } catch (error) {
    showError(error);
  }
}

initializeViewer();
