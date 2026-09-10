/* ============================================================
 *  LANDMARK CINEMAS – HINDI MOVIES SCRAPER  (Chrome Console JS)
 * ------------------------------------------------------------
 *  HOW TO RUN
 *    1. Open https://www.landmarkcinemas.com/ and log in normally
 *       (so the browser has the right session cookies).
 *    2. Open DevTools → Console.
 *    3. Paste this whole file and press Enter.
 *
 *  WHAT IT DOES
 *    • Fetches showtimes for all theatres (retry + backoff).
 *    • Filters Hindi films on CHECK_DATE.
 *    • Fetches seat maps ONE AT A TIME (concurrency = 1)
 *      with a 1–2 s delay between requests and per-request retries.
 *    • Prints detail / per-movie / per-theatre / overall tables.
 *    • Triggers a CSV download + stashes everything on
 *      window.LANDMARK_RESULTS for further inspection.
 *
 *  NOTES ON HEADERS (browser reality check)
 *    – User-Agent, sec-ch-ua, Accept-Language, Origin, Referer,
 *      Cookie etc. are FORBIDDEN header names in fetch() and
 *      cannot be set from the page. The browser supplies them
 *      automatically, including the HttpOnly session cookies.
 *    – We still rotate what we CAN set (X-Requested-With,
 *      Accept, Content-Type order, optional X-XSRF-TOKEN), and
 *      we re-read the XSRF token from the page/cookies before
 *      each request so it always matches the session.
 *    – If the API rejects the request with 400/403, set
 *      window.LANDMARK_XSRF_TOKEN = '<token from DevTools>'
 *      and re-run.
 * ============================================================ */

(async () => {
  'use strict';

  // =================== CONFIG ===================
  const CONFIG = {
    KEYWORD: 'hindi',
    CHECK_DATE: '2026-09-11',
    PRICE_MAP: { '2D': 14, 'IMAX': 18 },
    DEFAULT_PRICE: 15,

    MAX_RETRIES: 3,
    BACKOFF_BASE: 2.0,               // seconds: 2, 4, 8 (+jitter)
    SEATMAP_MIN_DELAY_MS: 1000,      // 1–2 s gap between seatmap calls
    SEATMAP_MAX_DELAY_MS: 2000,
    TIMEOUT_MS: 20000,

    DOWNLOAD_CSV: true,
    VERBOSE: true,
  };
  // ==============================================

  const THEATRES = {
    177: 'Campbell River', 180: 'Caledon, Bolton', 181: 'Brandon',
    182: 'Edmonton City Centre', 184: 'Calgary Country Hills',
    186: 'Winnipeg, Grant Park', 187: 'Surrey, Guildford',
    188: 'Hamilton, Jackson Square', 189: 'Kanata', 190: 'Kingston',
    191: 'Kitchener', 192: 'London', 193: 'Orleans',
    194: 'St. Catharines, Pen Centre', 195: 'Penticton',
    196: 'Calgary Shawnessy', 197: 'Spruce Grove', 200: 'Waterloo',
    201: 'Whitby', 202: 'Winkler', 203: 'Courtenay', 204: 'Cranbrook',
    206: 'Drayton Valley', 207: 'West Kelowna, Xtreme', 209: 'Fort St. John',
    211: 'Kelowna, Grand 10', 213: 'Nanaimo', 214: 'New Westminster',
    217: 'Sylvan Lake', 220: 'Port Alberni', 7779: 'Brooks',
    7782: 'Edson', 7784: 'West Kelowna, Encore', 7795: 'St. Albert',
    7796: 'Regina', 7798: 'Saskatoon', 7799: 'Fort McMurray Eagle Ridge',
    7800: 'Calgary Market Mall', 7801: 'Edmonton Tamarack', 7802: 'Windsor',
  };

  const BASE = location.origin;
  const MOVIES_API  = `${BASE}/Umbraco/Api/MovieApi/MoviesByCinema`;
  const SEATMAP_API = `${BASE}/Umbraco/Api/SeatMapApi/GetSessionSeatMap`;

  // =================== XSRF TOKEN ===================
  // The Python scraper reused a fixed token captured from DevTools.
  // Here we try hard to (re)discover it from the live page/cookies,
  // falling back to the last known value. If the server still
  // rejects the request, set window.LANDMARK_XSRF_TOKEN manually.
  const FALLBACK_XSRF =
    'o_CBOYZHVNJf0Xvp76dfIZg84NGBRa8Dj3MjHhWmDLaqUTf0yQjBHaT-Ae-z3-LZJ61t8oudyrVg97' +
    'BotC3_ll4hcI8xWGa5TUMf7h3O5iY1:_eT_jfhctaEiByvipV6AGIKSPNhttuLMpFPMCNwYnVnw4uE' +
    'NxzDDkBPPm2A_p2EbvDT3Gw3AAc22kHny3EcdWQ8vKn-AuI3Co85eVxqhHl8ObehY4TLIsIpEyW-mkwF' +
    'mPrs9DLjyk0eicn0-jTmg4Q2';

  function readCookie(name) {
    const esc = name.replace(/[.$?*|{}()[\]\\/+^]/g, '\\$&');
    const m = document.cookie.match(new RegExp('(?:^|; )' + esc + '=([^;]*)'));
    return m ? decodeURIComponent(m[1]) : null;
  }

  function findXsrfToken() {
    if (typeof window.LANDMARK_XSRF_TOKEN === 'string' && window.LANDMARK_XSRF_TOKEN.trim()) {
      return window.LANDMARK_XSRF_TOKEN.trim();
    }
    const meta = document.querySelector('meta[name="xsrf-token"], meta[name="XSRF-TOKEN"]');
    if (meta && meta.content) return meta.content;
    const inp = document.querySelector('input[name="__RequestVerificationToken"]');
    if (inp && inp.value) return inp.value;
    const c1 = readCookie('XSRF-TOKEN') || readCookie('X-XSRF-TOKEN');
    if (c1) return c1;
    return FALLBACK_XSRF;
  }

  let XSRF = findXsrfToken();
  console.log(`🔐 XSRF token length: ${XSRF ? XSRF.length : 0}`);

  // =================== HELPERS ===================
  const sleep = (ms) => new Promise(r => setTimeout(r, ms));
  const rnd = (a, b) => a + Math.random() * (b - a);
  const slugify = (s) => String(s).toLowerCase().replace(/[^a-z0-9]+/g, '-').replace(/^-|-$/g, '');
  const RETRYABLE = new Set([408, 425, 429, 500, 502, 503, 504]);

  // Rotating options we CAN set from the page.
  // (User-Agent / sec-ch-ua / Accept-Language / Cookie are browser-controlled.)
  const ROTATING = [
    { 'Accept': 'application/json, text/javascript, */*; q=0.01', 'X-Requested-With': 'XMLHttpRequest' },
    { 'Accept': 'application/json, text/plain, */*',              'X-Requested-With': 'XMLHttpRequest' },
    { 'Accept': '*/*',                                            'X-Requested-With': 'XMLHttpRequest' },
    { 'Accept': 'application/json, text/javascript, */*; q=0.01', 'X-Requested-With': 'XMLHttpRequest', 'Cache-Control': 'no-cache' },
  ];
  const pickRotatingHeaders = () => ({ ...ROTATING[Math.floor(Math.random() * ROTATING.length)] });

  // ---- fetch with timeout ----
  async function fetchWithTimeout(url, opts) {
    const ctrl = new AbortController();
    const timer = setTimeout(() => ctrl.abort(), CONFIG.TIMEOUT_MS);
    try {
      return await fetch(url, { ...opts, signal: ctrl.signal });
    } finally {
      clearTimeout(timer);
    }
  }

  // ---- generic retry ----
  async function requestWithRetry(url, opts, kind, extraLog) {
    let lastErr;
    for (let attempt = 1; attempt <= CONFIG.MAX_RETRIES; attempt++) {
      try {
        const r = await fetchWithTimeout(url, opts);
        if (RETRYABLE.has(r.status) || !r.ok) {
          throw new Error(`HTTP ${r.status} ${r.statusText}`);
        }
        return r;
      } catch (e) {
        lastErr = e;
        if (attempt >= CONFIG.MAX_RETRIES) break;
        const wait = (CONFIG.BACKOFF_BASE ** attempt) * 1000 + rnd(0, 1000);
        console.warn(`   🔁 ${kind} retry ${attempt}/${CONFIG.MAX_RETRIES - 1} in ${(wait / 1000).toFixed(1)}s — ${e.message}`, extraLog || '');
        await sleep(wait);
      }
    }
    throw lastErr;
  }

  // ---- LMC_Theatre* cookies (used by the server to build some links) ----
  function setTheatreCookies(cinemaId, theatreName) {
    const s = slugify(theatreName);
    const host = location.hostname;
    const exp = '; path=/; SameSite=Lax';
    document.cookie = `LMC_TheatreId=${cinemaId}${exp}`;
    document.cookie = `LMC_TheatreURL=${encodeURIComponent('/showtimes/' + s)}${exp}`;
    document.cookie = `LMC_TheatreName=${encodeURIComponent('/now-playing/' + s)}${exp}`;
    void host; // nothing else needed; browser handles the rest
  }

  // ---- 1–2 s serialised delay before every seatmap call ----
  let _lastSeatmap = 0;
  async function seatmapRateLimit() {
    const gap = rnd(CONFIG.SEATMAP_MIN_DELAY_MS, CONFIG.SEATMAP_MAX_DELAY_MS);
    const elapsed = Date.now() - _lastSeatmap;
    const wait = Math.max(0, gap - elapsed);
    if (wait > 0) await sleep(wait);
    _lastSeatmap = Date.now();
  }

  // =================== API CALLS ===================
  async function fetchMovies(cinemaId) {
    setTheatreCookies(cinemaId, THEATRES[cinemaId]);
    const params = new URLSearchParams({
      cinemaId: String(cinemaId),
      splitByAttributes: 'true',
      expandSessions: 'true',
    });
    const headers = {
      'Accept': 'application/json, text/plain, */*',
      'X-Requested-With': 'XMLHttpRequest',
    };
    if (XSRF) headers['x-xsrf-token'] = XSRF;

    const r = await requestWithRetry(
      `${MOVIES_API}?${params.toString()}`,
      { method: 'GET', headers, credentials: 'include' },
      'movies',
      { cinemaId, theatre: THEATRES[cinemaId] },
    );
    return r.json();
  }

  async function fetchSeatMap(sessionId, cinemaId, theatreName, ctx) {
    setTheatreCookies(cinemaId, theatreName);
    const payload = JSON.stringify({ SessionId: String(sessionId), CinemaId: Number(cinemaId) });

    let lastErr;
    for (let attempt = 1; attempt <= CONFIG.MAX_RETRIES; attempt++) {
      // Re-read the XSRF each attempt in case the site rotated it
      XSRF = findXsrfToken();
      const headers = {
        'Content-Type': 'application/json; charset=UTF-8',
        ...pickRotatingHeaders(),
      };
      if (XSRF) headers['x-xsrf-token'] = XSRF;

      try {
        const r = await fetchWithTimeout(SEATMAP_API, {
          method: 'POST',
          headers,
          body: payload,
          credentials: 'include',
        });
        const text = await r.text();
        if (RETRYABLE.has(r.status) || !r.ok) {
          throw new Error(`HTTP ${r.status} ${r.statusText}${text ? ' — ' + text.slice(0, 200) : ''}`);
        }
        try {
          return JSON.parse(text);
        } catch {
          throw new Error('Bad JSON: ' + text.slice(0, 200));
        }
      } catch (e) {
        lastErr = e;
        if (attempt >= CONFIG.MAX_RETRIES) break;
        const wait = (CONFIG.BACKOFF_BASE ** attempt) * 1000 + rnd(0, 1000);
        console.warn(`   🔁 seatmap retry ${attempt}/${CONFIG.MAX_RETRIES - 1} in ${(wait / 1000).toFixed(1)}s — ${e.message}`, ctx);
        await sleep(wait);
      }
    }
    throw lastErr;
  }

  // =================== PARSER ===================
  function parseSeatmap(data) {
    const out = {
      total: 0, sold: 0, available: 0, blocked: 0, wheelchair: 0,
      areas: [],
      result_code: data && data.ResultCode,
      result_message: (data && data.ResultMessage) || '',
    };
    const areas = (data && data.Data && data.Data.Area) || [];
    for (const area of areas) {
      const a = {
        id: area.AreaId, description: area.AreaDescription || '',
        total: 0, sold: 0, available: 0, blocked: 0, wheelchair: 0,
      };
      const rows = area.Rows || {};
      for (const rk in rows) {
        const seats = rows[rk].Seats || {};
        for (const sk in seats) {
          const seat = seats[sk];
          a.total++; out.total++;
          const st = seat.Status;
          if (st === 1) { a.sold++;      out.sold++; }
          else if (st === 0) { a.available++; out.available++; }
          else { a.blocked++; out.blocked++; }
          if (seat.Type === 2) { a.wheelchair++; out.wheelchair++; }
        }
      }
      out.areas.push(a);
    }
    return out;
  }

  // =================== MAIN ===================
  console.log(`%c🍁 Landmark Scraper — keyword="${CONFIG.KEYWORD}" date=${CONFIG.CHECK_DATE}`,
              'color:#e11;font-weight:bold;font-size:13px');
  console.log(`⚙️  retries=${CONFIG.MAX_RETRIES}  backoff=${CONFIG.BACKOFF_BASE}s  seatmap gap=${CONFIG.SEATMAP_MIN_DELAY_MS}-${CONFIG.SEATMAP_MAX_DELAY_MS}ms  concurrency=1`);
  const started = Date.now();

  const theatreIds = Object.keys(THEATRES).map(Number);
  console.log(`📥 Fetching showtimes for ${theatreIds.length} theatres...`);

  const allShows = [];

  for (const cid of theatreIds) {
    try {
      const movies = await fetchMovies(cid);
      console.log(`✅ [${THEATRES[cid]}] ${movies.length} film(s)`);
      for (const film of movies) {
        const title = film.Title || '';
        if (!title.toLowerCase().includes(CONFIG.KEYWORD)) continue;
        console.log(`🎯 [${THEATRES[cid]}] MATCH: "${title}" (FilmId=${film.FilmId})`);

        let added = 0;
        for (const sess of film.Sessions || []) {
          if (sess.NewDate !== CONFIG.CHECK_DATE) continue;
          for (const exp of sess.ExperienceTypes || []) {
            let lang = 'English', version = '2D';
            for (const a of exp.ExperienceAttributes || []) {
              const n = (a.Name || '').toLowerCase();
              if (n === 'language') lang = a.Value || lang;
              else if (n === 'version') version = a.Value || version;
            }
            for (const show of exp.Times || []) {
              allShows.push({
                theatreId: cid,
                theatreName: THEATRES[cid],
                movieTitle: title,
                filmId: film.FilmId,
                sessionId: show.Scheduleid,
                cinemaId: show.CinemaId || cid,
                showTime: show.StartTime,
                screen: show.Screen || '',
                lang, version,
              });
              added++;
            }
          }
        }
        if (added) console.log(`🎬 [${THEATRES[cid]}] "${title}" → ${added} show(s) on ${CONFIG.CHECK_DATE}`);
      }
    } catch (e) {
      console.error(`❌ Movies [${THEATRES[cid]}]: ${e.message}`);
    }
  }

  if (!allShows.length) {
    console.log('%c❌ No matching shows found.', 'color:#b00;font-weight:bold');
    return;
  }

  console.log(`\n🎬 ${allShows.length} show(s) found. Fetching seatmaps (concurrency=1, 1–2s gap)...\n`);

  const results = [];
  for (let i = 0; i < allShows.length; i++) {
    const show = allShows[i];
    const tag = `[${show.theatreName}] ${show.movieTitle} @${show.showTime} (session ${show.sessionId})`;
    console.log(`(${i + 1}/${allShows.length}) ${tag}`);

    await seatmapRateLimit();  // 1–2 s gap

    try {
      const data = await fetchSeatMap(show.sessionId, show.cinemaId, show.theatreName, tag);
      const stats = parseSeatmap(data);
      const price = CONFIG.PRICE_MAP[show.version] ?? CONFIG.DEFAULT_PRICE;
      const occ   = stats.total ? +((stats.sold / stats.total) * 100).toFixed(2) : 0;
      const gross = price * stats.sold;
      const areaStr = stats.areas.map(a => `${a.description}:${a.sold}/${a.total}`).join(' | ');

      console.log(`   💺 sold=${stats.sold}/${stats.total} (${occ}%) [wc=${stats.wheelchair}] ${areaStr}`);

      results.push({
        ...show,
        sold: stats.sold, available: stats.available, total: stats.total,
        blocked: stats.blocked, wheelchair: stats.wheelchair,
        occupancy: occ, avgPrice: price, gross,
        areas: stats.areas,
        result_code: stats.result_code, result_message: stats.result_message,
        error: null,
      });
    } catch (e) {
      console.error(`   ❌ ${e.message}`);
      results.push({
        ...show, sold: 0, available: 0, total: 0, blocked: 0, wheelchair: 0,
        occupancy: 0, avgPrice: 0, gross: 0, areas: [],
        result_code: null, result_message: '', error: e.message,
      });
    }
  }

  const valid  = results.filter(r => !r.error);
  const failed = results.filter(r =>  r.error);
  console.log(`\nℹ️  Seat summary: %c${valid.length} ok%c / %c${failed.length} failed`,
              'color:#090;font-weight:bold', '', 'color:#b00;font-weight:bold');
  if (failed.length) console.table(failed.map(f => ({ Movie: f.movieTitle, Theatre: f.theatreName, Session: f.sessionId, Error: f.error })));

  if (!valid.length) {
    console.log('%c❌ No seat data.', 'color:#b00;font-weight:bold');
    return;
  }

  // ---- detail ----
  const detail = valid.map(s => ({
    Movie: s.movieTitle,
    Theatre: s.theatreName,
    'Show Time': s.showTime,
    Lang: s.lang,
    Ver: s.version,
    Sold: s.sold,
    Available: s.available,
    Total: s.total,
    Wheelchair: s.wheelchair,
    'Occ %': s.occupancy,
    Gross: +s.gross.toFixed(2),
    Areas: s.areas.map(a => `${a.description}:${a.sold}/${a.total}`).join(' | '),
    ResultCode: s.result_code,
  }));
  console.log('\n📊 DETAILED SHOW DATA');
  console.table(detail);

  // ---- per-movie ----
  const movieMap = {};
  for (const s of valid) {
    const k = s.movieTitle;
    movieMap[k] = movieMap[k] || { Movie: k, Shows: 0, Sold: 0, Total: 0, Gross: 0 };
    movieMap[k].Shows++; movieMap[k].Sold += s.sold;
    movieMap[k].Total += s.total; movieMap[k].Gross += s.gross;
  }
  const movieRows = Object.values(movieMap)
    .sort((a, b) => b.Gross - a.Gross)
    .map(m => ({ ...m, 'Occ %': m.Total ? +((m.Sold / m.Total) * 100).toFixed(2) : 0, Gross: +m.Gross.toFixed(2) }));
  console.log('\n🎬 PER-MOVIE SUMMARY');
  console.table(movieRows);

  // ---- per-theatre ----
  const theatreMap = {};
  for (const s of valid) {
    const k = s.theatreName;
    theatreMap[k] = theatreMap[k] || { Theatre: k, Shows: 0, Sold: 0, Total: 0, Gross: 0 };
    theatreMap[k].Shows++; theatreMap[k].Sold += s.sold;
    theatreMap[k].Total += s.total; theatreMap[k].Gross += s.gross;
  }
  const theatreRows = Object.values(theatreMap)
    .sort((a, b) => b.Gross - a.Gross)
    .map(t => ({ ...t, 'Occ %': t.Total ? +((t.Sold / t.Total) * 100).toFixed(2) : 0, Gross: +t.Gross.toFixed(2) }));
  console.log('\n🏢 PER-THEATRE SUMMARY');
  console.table(theatreRows);

  // ---- overall ----
  const overall = valid.reduce((a, s) => ({
    shows: a.shows + 1, sold: a.sold + s.sold,
    total: a.total + s.total, gross: a.gross + s.gross,
  }), { shows: 0, sold: 0, total: 0, gross: 0 });

  console.log('\n📈 OVERALL SUMMARY');
  console.log(`Total Shows:       ${overall.shows}`);
  console.log(`Total Sold Seats:  ${overall.sold}`);
  console.log(`Total Capacity:    ${overall.total}`);
  console.log(`Overall Occupancy: ${overall.total ? ((overall.sold / overall.total) * 100).toFixed(2) : 0}%`);
  console.log(`Total Est. Gross:  $${overall.gross.toFixed(2)} CAD`);
  console.log(`⏱️  Run time: ${((Date.now() - started) / 1000).toFixed(2)} s`);

  // ---- CSV ----
  function toCSV(rows) {
    if (!rows.length) return '';
    const headers = Object.keys(rows[0]);
    const esc = (v) => {
      const s = v == null ? '' : String(v);
      return /[",\n]/.test(s) ? `"${s.replace(/"/g, '""')}"` : s;
    };
    return [headers.join(','),
            ...rows.map(r => headers.map(h => esc(r[h])).join(','))].join('\n');
  }
  const csv = toCSV(detail);
  window.LANDMARK_RESULTS = {
    config: CONFIG, detail, movieRows, theatreRows, overall,
    raw: results, csv, generatedAt: new Date().toISOString(),
  };
  console.log('%c💾 Results saved to window.LANDMARK_RESULTS (detail, movieRows, theatreRows, overall, raw, csv).',
              'color:#06c');

  if (CONFIG.DOWNLOAD_CSV && csv) {
    const blob = new Blob([csv], { type: 'text/csv;charset=utf-8' });
    const url = URL.createObjectURL(blob);
    const a = document.createElement('a');
    a.href = url;
    a.download = 'landmark_hindi_results.csv';
    document.body.appendChild(a);
    a.click();
    a.remove();
    setTimeout(() => URL.revokeObjectURL(url), 5000);
    console.log('📥 CSV download triggered.');
  }
})();
