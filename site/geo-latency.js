(function () {
  "use strict";

  var DATA_URL = "./geo-latency-data.json";

  function $(id) { return document.getElementById(id); }

  function rttClass(ms) {
    if (ms == null) return "rtt-na";
    if (ms < 100) return "rtt-near";
    if (ms < 200) return "rtt-mid";
    return "rtt-far";
  }

  function rttLabel(ms) {
    return ms == null ? "n/a" : ms + " ms";
  }

  var CONTINENT_COLORS = {
    EU: "var(--geo-eu, #6c8cff)",
    NA: "var(--geo-na, #4fd1c5)",
    APAC: "var(--geo-apac, #f6c177)",
    Other: "var(--geo-other, #9aa0b4)"
  };

  var CONTINENT_HEX = {
    EU: "#6c8cff", NA: "#4fd1c5", APAC: "#f6c177", Other: "#9aa0b4"
  };

  function renderHeadline(d) {
    var h = d.headline || {};
    // BFT-correct framing: the 33% liveness line applies to ONE correlated-failure domain, so
    // headline the LARGEST SINGLE provider (one ASN) against it — NOT the sum of the top two
    // (two separate providers = two independent failure domains). Top-2/top-4 are shown only as
    // concentration DEPTH context, not against the BFT line.
    var bft = h.bft_threshold_pct || 33.3;
    var provs = d.providers || [];
    var shortName = function (n) { return String(n || "").split(/[\s,]+/)[0]; };
    var top1 = provs[0] || { name: "-", stake_pct: 0 };
    var over = top1.stake_pct >= bft;

    // concentration depth: how many providers combined to first cross 1/3 of stake
    var depth = provs.length;
    for (var i = 0; i < provs.length; i++) {
      if (provs[i].cum_pct >= bft) { depth = i + 1; break; }
    }

    var lbl = $("hl-top2-label");
    if (lbl) lbl.textContent = "Largest provider";
    $("hl-hetzner-ovh").textContent = top1.stake_pct + "%";

    // second tile: top country by stake (jurisdiction concentration) — replaced top-4 ASNs,
    // which duplicated the provider-concentration card below.
    var countries = d.countries || [];
    var topC = countries[0] || { name: "-", stake_pct: 0, count: 0 };
    $("hl-top-country").textContent = topC.stake_pct + "%";
    var cSub = $("hl-top-country-sub");
    if (cSub) cSub.textContent = topC.name + " · " + topC.count + " nodes";

    var sub = $("hl-hetzner-ovh-sub");
    if (sub) {
      sub.textContent = shortName(top1.name) + " \u00b7 " +
        (over ? "past" : "below") + " the 33% BFT line";
    }
    // red-alert styling only when a single provider actually crosses the line
    var card = lbl ? lbl.closest(".geo-stat") : null;
    if (card) card.classList.toggle("geo-stat-alert", over);

    // threshold bar tracks the largest SINGLE provider vs 33%
    var fill = $("threshold-fill");
    fill.style.width = Math.min(100, top1.stake_pct) + "%";
    fill.classList.toggle("is-over", over);

    var note = $("threshold-note");
    if (note) {
      note.innerHTML = "The largest single hosting provider (<strong>" + shortName(top1.name) +
        "</strong>) holds <strong>" + top1.stake_pct + "%</strong> of stake \u2014 " +
        (over
          ? "past the " + bft + "% line, where one correlated provider failure can threaten BFT liveness."
          : "below the " + bft + "% line, so no single provider failure alone crosses the 1/3 liveness threshold.") +
        " It takes <strong>" + depth + "</strong> providers combined to reach 1/3 of stake.";
    }

    // --- Blast radius: largest correlated-failure domain at each aggregation level ---
    var EU_CC = { AT:1,BE:1,BG:1,HR:1,CY:1,CZ:1,DK:1,EE:1,FI:1,FR:1,DE:1,GR:1,HU:1,IE:1,IT:1,
      LV:1,LT:1,LU:1,MT:1,NL:1,PL:1,PT:1,RO:1,SK:1,SI:1,ES:1,SE:1 };
    var euPct = 0;
    for (var ci = 0; ci < countries.length; ci++) {
      if (EU_CC[countries[ci].cc]) euPct += countries[ci].stake_pct;
    }
    euPct = Math.round(euPct * 100) / 100;

    var cap = $("continent-caption");
    if (cap) {
      cap.innerHTML = "The EU, as one legal jurisdiction, holds <strong>" + euPct + "%</strong> of stake — " +
        (euPct >= bft ? "past" : "below") + " the 1/3 BFT line. Jurisdiction, not any single provider or " +
        "country, is the largest correlated-failure domain.";
    }

    var ladder = $("blast-ladder");
    if (ladder) {
      var rows = [
        { label: "Largest provider", name: shortName(top1.name), pct: top1.stake_pct },
        { label: "Largest country", name: topC.name, pct: topC.stake_pct },
        { label: "EU (legal jurisdiction)", name: "27 states", pct: euPct }
      ];
      ladder.innerHTML = rows.map(function (r, idx) {
        var o = r.pct >= bft;
        var lineLabel = idx === 0 ? "<span>1/3</span>" : "";
        return '<div class="geo-blast-row">' +
          '<span class="geo-blast-label">' + r.label + ' <em>' + r.name + '</em></span>' +
          '<div class="geo-threshold-track">' +
            '<div class="geo-threshold-fill' + (o ? " is-over" : "") + '" style="width:' + Math.min(100, r.pct) + '%"></div>' +
            '<div class="geo-threshold-line" style="left:33.3%">' + lineLabel + '</div>' +
          '</div>' +
          '<span class="geo-blast-val' + (o ? " is-over" : "") + '">' + r.pct + '%</span>' +
          '</div>';
      }).join("");
    }
  }

  var SIGNAL_ICON = { critical: "⚠", warn: "▲", info: "●", good: "✓" };

  function renderSignals(d) {
    var el = $("signal-list");
    if (!el) return;
    var card = $("geo-signals");
    var sigs = d.signals || [];
    if (!sigs.length) {
      el.innerHTML = "";
      if (card) card.style.display = "none";
      return;
    }
    if (card) card.style.display = "";
    el.innerHTML = sigs.map(function (g) {
      var lvl = g.level || "info";
      return '<li class="signal signal-' + lvl + '">' +
        '<span class="signal-dot" aria-hidden="true">' + (SIGNAL_ICON[lvl] || SIGNAL_ICON.info) + '</span>' +
        '<span class="signal-text">' + escapeHtml(g.text) + '</span></li>';
    }).join("");
  }

  function statBig(main, sub) {
    return main + '<small class="stat-sub"> / ' + sub + '</small>';
  }

  function renderCoverage(d) {
    var c = d.coverage || {};
    $("meta-epoch").textContent = d.epoch != null ? d.epoch : "-";
    $("coverage-badge").textContent = d.generated_at_utc ? "Updated " + d.generated_at_utc : "snapshot";
    $("hl-located").innerHTML = statBig(c.geo_located, c.total_validators);
    $("hl-reachable").innerHTML = statBig(c.rtt_reachable_ips, c.rtt_total_ips);
  }

  function renderDonut(d) {
    var el = $("continent-donut");
    if (!el || !d.continents) return;
    var total = d.continents.reduce(function (s, c) { return s + c.stake_pct; }, 0) || 100;
    var acc = 0, stops = [];
    d.continents.forEach(function (c) {
      var start = acc / total * 100;
      acc += c.stake_pct;
      var end = acc / total * 100;
      var col = CONTINENT_HEX[c.name] || CONTINENT_HEX.Other;
      stops.push(col + " " + start.toFixed(2) + "% " + end.toFixed(2) + "%");
    });
    el.style.background = "conic-gradient(" + stops.join(",") + ")";
    var dom = d.continents.slice().sort(function (a, b) { return b.stake_pct - a.stake_pct; })[0];
    el.innerHTML = '<div class="donut-hole"><strong>' + Math.round(dom.stake_pct) +
      '%</strong><span>' + dom.name + '</span></div>';
    $("continent-legend").innerHTML = d.continents.map(function (c) {
      return '<li><i class="dl-dot" style="background:' + (CONTINENT_HEX[c.name] || CONTINENT_HEX.Other) + '"></i>' +
        '<span class="dl-name">' + c.name + '</span>' +
        '<span class="dl-val">' + c.stake_pct.toFixed(1) + '%</span>' +
        '<span class="dl-sub">' + c.count + ' nodes \u00b7 ' + rttLabel(c.rtt_ms) + '</span></li>';
    }).join("");
  }

  function renderMiniBars(elId, rows) {
    var el = $(elId);
    if (!el || !rows.length) return;
    var max = Math.max.apply(null, rows.map(function (r) { return r.value; }));
    el.innerHTML = rows.map(function (r) {
      var w = max > 0 ? (r.value / max * 100) : 0;
      return '<div class="minibar" title="' + r.title + '">' +
        '<div class="mb-label">' + r.label + '</div>' +
        '<div class="mb-track"><div class="mb-fill" style="width:' + w + '%;background:' + r.color + '"></div></div>' +
        '<div class="mb-val">' + (r.display != null ? r.display : r.value.toFixed(1) + '%') + '</div></div>';
    }).join("");
  }

  var STAKE_FILL = "linear-gradient(90deg, #8b5cf6, #22d3ee)";

  function renderLatency(d) {
    var rows = d.continents.filter(function (c) { return c.rtt_ms != null; })
      .slice()
      .sort(function (a, b) { return a.rtt_ms - b.rtt_ms; })
      .map(function (c) {
        return {
          label: c.name,
          value: c.rtt_ms,
          display: c.rtt_ms + " ms",
          color: RTT_FILL[rttClass(c.rtt_ms)],
          title: c.name + " \u00b7 " + c.rtt_ms + " ms stake-weighted median from " + (VANTAGE_WORDS[currentNet] || VANTAGE_WORDS.testnet).short
        };
      });
    renderMiniBars("latency-bars", rows);

    var cap = $("latency-caption");
    var rounds = d.coverage && d.coverage.rtt_rounds;
    if (cap && rounds) {
      var vw = (VANTAGE_WORDS[currentNet] || VANTAGE_WORDS.testnet).vantage;
      cap.textContent = "Stake-weighted median ICMP round-trip from our " + vw + ", by continent. " +
        "Average of " + rounds + " measurement round" + (rounds === 1 ? "" : "s") + " this epoch.";
    }
  }

  function renderTopCountries(d) {
    var top = d.countries.slice(0, 8);
    var rows = top.map(function (c) {
      return {
        label: '<span class="geo-flag">' + flag(c.cc) + '</span>' + c.name,
        value: c.stake_pct,
        color: STAKE_FILL,
        title: c.name + " \u00b7 " + c.stake_pct.toFixed(2) + "% stake \u00b7 " + rttLabel(c.rtt_ms) + " from " + vShort()
      };
    });
    renderMiniBars("country-minibars", rows);

    var foot = $("country-foot");
    if (foot) {
      var cum = top.reduce(function (s, c) { return s + c.stake_pct; }, 0);
      var w = Math.min(100, cum).toFixed(1);
      var mono = d.countries.filter(function (c) { return c.top_provider_pct >= 99; })
        .slice(0, 3)
        .map(function (c) {
          return c.name + " is 100% " + c.top_provider.split(/[\s,]/)[0];
        });
      var note = mono.length
        ? "Diverse by flag, monoculture by ASN: <b>" + mono.join("</b>, <b>") +
          "</b>. Several country lines are a single hosting provider."
        : "Country spread looks healthy, but stake collapses onto a few hosting ASNs.";
      foot.innerHTML =
        '<div class="foot-scale">' +
          '<div class="foot-scale-track"><div class="foot-scale-fill" style="width:' + w + '%"></div></div>' +
          '<div class="foot-scale-labels"><span>Top 8 countries</span>' +
            '<span><b>' + cum.toFixed(1) + '%</b> of located stake</span></div>' +
        '</div>' +
        '<p class="foot-note">' + note + '</p>';
    }
  }

  function renderProviderMini(d) {
    var rows = d.providers.slice(0, 8).map(function (p) {
      return {
        label: p.name,
        value: p.stake_pct,
        color: STAKE_FILL,
        title: p.name + " \u00b7 " + p.stake_pct.toFixed(2) + "% stake \u00b7 cum " +
          p.cum_pct.toFixed(1) + "% \u00b7 " + rttLabel(p.rtt_ms) + " from " + vShort()
      };
    });
    renderMiniBars("provider-minibars", rows);
  }

  function renderContinents(d) {
    var wrap = $("continent-bars");
    if (!wrap) return;
    wrap.innerHTML = "";
    var max = Math.max.apply(null, d.continents.map(function (c) { return c.stake_pct; }));
    d.continents.forEach(function (c) {
      var row = document.createElement("div");
      row.className = "geo-bar-row";
      var w = max > 0 ? (c.stake_pct / max * 100) : 0;
      row.innerHTML =
        '<div class="geo-bar-head">' +
          '<span class="geo-bar-name">' + c.name + '</span>' +
          '<span class="geo-bar-rtt ' + rttClass(c.rtt_ms) + '">' + rttLabel(c.rtt_ms) + ' from ' + vShort() + '</span>' +
        '</div>' +
        '<div class="geo-bar-track">' +
          '<div class="geo-bar-fill" style="width:' + w + '%;background:' +
            (CONTINENT_COLORS[c.name] || CONTINENT_COLORS.Other) + '"></div>' +
          '<span class="geo-bar-value">' + c.stake_pct.toFixed(1) + '% stake</span>' +
        '</div>' +
        '<div class="geo-bar-sub">' + c.count + ' nodes \u00b7 ' + c.count_pct + '% by count</div>';
      wrap.appendChild(row);
    });
  }

  function flag(cc) {
    if (!cc || cc.length !== 2) return "";
    var base = 0x1F1E6;
    return String.fromCodePoint(base + cc.charCodeAt(0) - 65) +
           String.fromCodePoint(base + cc.charCodeAt(1) - 65);
  }

  var countryData = [];
  var countrySort = { key: "top_provider_pct", dir: -1 };

  function drawCountryRows() {
    var tb = $("country-tbody");
    if (!tb) return;
    var k = countrySort.key, dir = countrySort.dir;
    var rows = countryData.slice().sort(function (a, b) {
      var av = a[k], bv = b[k];
      if (av == null && bv == null) return 0;
      if (av == null) return 1;
      if (bv == null) return -1;
      if (typeof av === "string") return dir * av.localeCompare(bv);
      return dir * (av - bv);
    });
    var maxStake = Math.max.apply(null, countryData.map(function (c) { return c.stake_pct; })) || 1;
    tb.innerHTML = "";
    rows.forEach(function (c) {
      var tr = document.createElement("tr");
      var mono = c.top_provider_pct >= 99;
      tr.innerHTML =
        '<td><span class="prov-share' + (mono ? ' is-mono' : '') + '">' +
          c.top_provider_pct.toFixed(0) + '%</span> ' + escapeHtml(c.top_provider) +
          ' <small class="prov-count">' + c.provider_count + ' ASN' + (c.provider_count === 1 ? '' : 's') + '</small></td>' +
        '<td><span class="geo-flag">' + flag(c.cc) + '</span>' + c.name + '</td>' +
        '<td class="num"><span class="stake-cell"><i style="width:' +
          (c.stake_pct / maxStake * 100).toFixed(1) + '%"></i><strong>' +
          c.stake_pct.toFixed(2) + '%</strong></span></td>' +
        '<td class="num">' + c.count + '</td>' +
        '<td class="num"><span class="rtt-chip ' + rttClass(c.rtt_ms) + '">' + rttLabel(c.rtt_ms) + '</span></td>';
      tb.appendChild(tr);
    });
  }

  function updateSortHeads() {
    document.querySelectorAll("#countries th.sortable").forEach(function (th) {
      th.classList.remove("sorted-asc", "sorted-desc");
      if (th.getAttribute("data-key") === countrySort.key) {
        th.classList.add(countrySort.dir === 1 ? "sorted-asc" : "sorted-desc");
      }
    });
  }

  function renderCountries(d) {
    countryData = d.countries || [];
    document.querySelectorAll("#countries th.sortable").forEach(function (th) {
      th.addEventListener("click", function () {
        var k = th.getAttribute("data-key");
        if (countrySort.key === k) {
          countrySort.dir = -countrySort.dir;
        } else {
          countrySort.key = k;
          countrySort.dir = k === "name" ? 1 : -1;
        }
        updateSortHeads();
        drawCountryRows();
      });
    });
    updateSortHeads();
    drawCountryRows();
  }

  function renderProviders(d) {
    var tb = $("provider-tbody");
    if (!tb) return;
    tb.innerHTML = "";
    d.providers.forEach(function (p) {
      var tr = document.createElement("tr");
      var over = p.cum_pct >= (d.headline.bft_threshold_pct || 33.3);
      tr.innerHTML =
        '<td><span class="geo-asn">' + (p.asn || "") + '</span>' + p.name + '</td>' +
        '<td class="num"><strong>' + p.stake_pct.toFixed(2) + '%</strong></td>' +
        '<td class="num ' + (over ? "cum-over" : "") + '">' + p.cum_pct.toFixed(1) + '%</td>' +
        '<td class="num">' + p.count + '</td>' +
        '<td class="num"><span class="rtt-chip ' + rttClass(p.rtt_ms) + '">' + rttLabel(p.rtt_ms) + '</span></td>';
      tb.appendChild(tr);
    });
  }

  var RTT_FILL = {
    "rtt-near": "#10b981",
    "rtt-mid": "#f59e0b",
    "rtt-far": "#ef4444",
    "rtt-na": "#94a3b8"
  };

  function bubbleRadius(stakePct) {
    return Math.max(6, Math.min(46, Math.sqrt(stakePct) * 9));
  }

  function escapeHtml(s) {
    return String(s).replace(/[&<>"']/g, function (c) {
      return { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c];
    });
  }

  function popupHtml(p) {
    var provs = (p.providers || []).map(function (pr) {
      return '<li><span>' + escapeHtml(pr.name) + '</span><b>' + pr.loc_pct + '%</b>' +
        '<small>' + pr.count + ' node' + (pr.count === 1 ? '' : 's') + '</small></li>';
    }).join("");
    var rtt = p.rtt_ms == null ? "no ICMP" : p.rtt_ms + " ms";
    return '' +
      '<div class="map-pop">' +
        '<div class="map-pop-head">' + escapeHtml(p.city) + ', ' + escapeHtml(p.cc) + '</div>' +
        '<div class="map-pop-sub">' + escapeHtml(p.country) + '</div>' +
        '<div class="map-pop-stats">' +
          '<span><b>' + p.stake_pct.toFixed(2) + '%</b> stake</span>' +
          '<span><b>' + p.count + '</b> node' + (p.count === 1 ? '' : 's') + '</span>' +
          '<span><b>' + rtt + '</b> from ' + vShort() + '</span>' +
        '</div>' +
        '<div class="map-pop-prov-title">Hosting providers</div>' +
        '<ul class="map-pop-prov">' + provs + '</ul>' +
      '</div>';
  }

  var geoMap = null;
  function renderMap(d) {
    var el = $("geo-map");
    if (!el) return;
    if (typeof L === "undefined" || !d.map_points || !d.map_points.length) {
      var fb = $("map-fallback"); if (fb) fb.hidden = false;
      el.style.display = "none";
      return;
    }
    var fb2 = $("map-fallback"); if (fb2) fb2.hidden = true;
    el.style.display = "";
    if (geoMap) { try { geoMap.remove(); } catch (e) {} geoMap = null; }
    el._leaflet_id = null; el.innerHTML = "";
    var map = L.map(el, {
      worldCopyJump: true,
      minZoom: 2,
      maxZoom: 7,
      scrollWheelZoom: false,
      attributionControl: false
    }).setView([30, 10], 2);
    geoMap = map;

    L.tileLayer("https://{s}.basemaps.cartocdn.com/dark_all/{z}/{x}/{y}{r}.png", {
      subdomains: "abcd",
      maxZoom: 7
    }).addTo(map);

    map.on("focus", function () { map.scrollWheelZoom.enable(); });
    map.on("blur", function () { map.scrollWheelZoom.disable(); });

    d.map_points.forEach(function (p) {
      var cls = rttClass(p.rtt_ms);
      var marker = L.circleMarker([p.lat, p.lon], {
        radius: bubbleRadius(p.stake_pct),
        color: RTT_FILL[cls],
        weight: 1.5,
        opacity: 0.9,
        fillColor: RTT_FILL[cls],
        fillOpacity: 0.45
      }).addTo(map);
      marker.bindPopup(popupHtml(p), { className: "geo-map-popup", maxWidth: 260 });
      marker.bindTooltip(
        p.city + " \u00b7 " + p.stake_pct.toFixed(1) + "% stake",
        { direction: "top", opacity: 0.9 }
      );
      marker.on("popupopen", function () {
        marker.setStyle({ weight: 3, color: "#ffffff", fillOpacity: 0.75 });
      });
      marker.on("popupclose", function () {
        marker.setStyle({ weight: 1.5, color: RTT_FILL[cls], fillOpacity: 0.45 });
      });
    });

    var vp = d.vantage_point;
    if (vp && vp.lat != null && vp.lon != null) {
      var vIcon = L.divIcon({
        className: "vantage-marker",
        html: '<span class="vantage-pulse"></span><span class="vantage-core"></span>' +
              '<span class="vantage-label">' + escapeHtml(vp.label || "Our node") + '</span>',
        iconSize: [16, 16],
        iconAnchor: [8, 8]
      });
      var vm = L.marker([vp.lat, vp.lon], { icon: vIcon, zIndexOffset: 1000 }).addTo(map);
      vm.bindPopup(
        '<div class="map-pop">' +
          '<div class="map-pop-head">' + escapeHtml(vp.label || "Our node") + '</div>' +
          '<div class="map-pop-sub">Measurement vantage \u2014 every RTT on this page is measured from here.</div>' +
        '</div>',
        { className: "geo-map-popup", maxWidth: 240 }
      );
    }
  }

  function fail(msg) {
    ["continent-bars"].forEach(function (id) {
      var el = $(id); if (el) el.innerHTML = '<p class="geo-error">' + msg + '</p>';
    });
  }

  Array.prototype.forEach.call(document.querySelectorAll(".info-btn"), function (btn) {
    btn.addEventListener("click", function (e) {
      e.stopPropagation();
      var w = btn.closest(".info-wrap");
      var open = w.classList.toggle("open");
      btn.setAttribute("aria-expanded", open ? "true" : "false");
    });
  });
  // --- Trends over time (Phase 2): lightweight inline-SVG line charts, no deps ---
  function svgLineChart(series, opts) {
    var W = 540, H = 160, pad = { l: 30, r: 10, t: 10, b: 8 };
    var yMax = opts.yMax || 100, n = opts.n;
    var iw = W - pad.l - pad.r, ih = H - pad.t - pad.b;
    // The latest point always sits at the right edge; with a single day, anchor
    // it there (not mid-chart) so it reads as "trend starts here", not a stray dot.
    function X(i) { return pad.l + (n <= 1 ? iw : (i / (n - 1)) * iw); }
    function Y(v) { return pad.t + ih - (Math.max(0, Math.min(yMax, v)) / yMax) * ih; }
    var grid = "";
    [0, 0.25, 0.5, 0.75, 1].forEach(function (f) {
      var y = pad.t + ih - f * ih;
      grid += '<line x1="' + pad.l + '" y1="' + y + '" x2="' + (W - pad.r) + '" y2="' + y + '" class="tl-grid"/>';
      grid += '<text x="2" y="' + (y + 3) + '" class="tl-ytick">' + Math.round(f * yMax) + '</text>';
    });
    var paths = series.map(function (s) {
      if (n === 1) {
        var y = Y(s.points[0]).toFixed(1), xr = X(0).toFixed(1);
        // dashed "current level" guide across the plot + the dot at the right edge
        return '<line x1="' + pad.l + '" y1="' + y + '" x2="' + xr + '" y2="' + y +
          '" stroke="' + s.color + '" stroke-width="1.5" stroke-dasharray="3 4" opacity="0.4"/>' +
          '<circle cx="' + xr + '" cy="' + y + '" r="3.5" fill="' + s.color + '"/>';
      }
      var dd = s.points.map(function (v, i) { return (i ? "L" : "M") + X(i).toFixed(1) + " " + Y(v).toFixed(1); }).join(" ");
      return '<path d="' + dd + '" fill="none" stroke="' + s.color + '" stroke-width="2" stroke-linejoin="round"/>';
    }).join("");
    // width/height attributes give the SVG an intrinsic aspect ratio so a CSS-grid
    // row can size to its real rendered height (CSS width:100% still scales it).
    // Without them the grid mis-sizes the row and the legend spills past the card.
    return '<svg viewBox="0 0 ' + W + " " + H + '" width="' + W + '" height="' + H +
      '" class="tl-svg" role="img">' + grid + paths + "</svg>";
  }

  function deltaFor(hist, points, days) {
    var n = hist.length;
    if (n < 2) return null;
    var target = hist[n - 1].ts - days * 86400;
    var idx = null;
    for (var i = 0; i < n; i++) { if (hist[i].ts <= target) idx = i; }
    if (idx == null) return null; // no entry old enough yet
    return points[n - 1] - points[idx];
  }

  function trendLegend(series) {
    function chip(dv, lbl) {
      if (dv == null) return '<span class="tl-delta tl-na">' + lbl + ' –</span>';
      var cls = dv > 0.05 ? "up" : (dv < -0.05 ? "down" : "flat");
      var sign = dv > 0 ? "+" : "";
      return '<span class="tl-delta tl-' + cls + '">' + lbl + " " + sign + dv.toFixed(1) + "</span>";
    }
    return '<ul class="tl-legend">' + series.map(function (s) {
      var last = s.points[s.points.length - 1];
      return '<li><i class="tl-swatch" style="background:' + s.color + '"></i>' +
        '<span class="tl-name">' + escapeHtml(s.name) + "</span>" +
        "<b>" + last.toFixed(1) + "%</b>" + chip(s.d7, "7d") + chip(s.d30, "30d") + "</li>";
    }).join("") + "</ul>";
  }

  var TREND_PALETTE = ["#8b5cf6", "#22d3ee", "#f6c177", "#4fd1c5", "#f472b6"];
  var HISTORY_FEEDS = { testnet: "./geo-latency-history.json", mainnet: "./geo-latency-history-mainnet.json" };

  function renderTrends(net) {
    var host1 = $("trend-continents"), host2 = $("trend-countries"), status = $("trend-status");
    if (!host1 || !host2) return;
    fetch(HISTORY_FEEDS[net], { cache: "no-cache" })
      .then(function (r) { if (!r.ok) throw new Error(r.status); return r.json(); })
      .then(function (hist) {
        if (net !== currentNet) return; // tab switched mid-fetch
        if (!Array.isArray(hist) || !hist.length) { throw new Error("empty"); }
        var n = hist.length;
        if (status) {
          status.textContent = n < 2
            ? "Collecting history — trends fill in as days accumulate (" + n + " day so far)."
            : n + " days of history; 7d / 30d deltas appear once enough days exist.";
        }
        var contNames = ["EU", "NA", "APAC", "Other"];
        var cSeries = contNames.map(function (nm) {
          var pts = hist.map(function (day) {
            var c = (day.continents || []).filter(function (x) { return x.name === nm; })[0];
            return c ? c.stake_pct : 0;
          });
          return { name: nm, color: CONTINENT_HEX[nm] || CONTINENT_HEX.Other, points: pts,
            d7: deltaFor(hist, pts, 7), d30: deltaFor(hist, pts, 30) };
        });
        host1.innerHTML = '<div class="trend-canvas">' + svgLineChart(cSeries, { yMax: 100, n: n }) + '</div>' + trendLegend(cSeries);

        var top = (hist[n - 1].countries || []).slice(0, 5);
        var kSeries = top.map(function (c, i) {
          var pts = hist.map(function (day) {
            var m = (day.countries || []).filter(function (x) { return x.cc === c.cc; })[0];
            return m ? m.stake_pct : 0;
          });
          return { name: c.name, color: TREND_PALETTE[i % TREND_PALETTE.length], points: pts,
            d7: deltaFor(hist, pts, 7), d30: deltaFor(hist, pts, 30) };
        });
        var peak = 20;
        kSeries.forEach(function (s) { s.points.forEach(function (v) { if (v > peak) peak = v; }); });
        var yMax = Math.ceil(peak / 10) * 10;
        host2.innerHTML = '<div class="trend-canvas">' + svgLineChart(kSeries, { yMax: yMax, n: n }) + '</div>' + trendLegend(kSeries);
      })
      .catch(function () {
        if (net !== currentNet) return;
        if (status) status.textContent = "History feed is not available yet — it begins on the next refresh.";
        host1.innerHTML = ""; host2.innerHTML = "";
      });
  }

  var FEEDS = { testnet: "./geo-latency-data.json", mainnet: "./geo-latency-data-mainnet.json" };
  var currentNet = null;
  function vShort() { return (VANTAGE_WORDS[currentNet] || VANTAGE_WORDS.testnet).short; }
  var VANTAGE_WORDS = {
    testnet: { place: "Prague", short: "CZ", vantage: "Prague server" },
    mainnet: { place: "Prague", short: "CZ", vantage: "Prague server" }
  };
  function applyVantageWording(net) {
    var w = VANTAGE_WORDS[net] || VANTAGE_WORDS.testnet;
    Array.prototype.forEach.call(document.querySelectorAll(".v-place"),   function (el) { el.textContent = w.place; });
    Array.prototype.forEach.call(document.querySelectorAll(".v-short"),   function (el) { el.textContent = w.short; });
    Array.prototype.forEach.call(document.querySelectorAll(".v-vantage"), function (el) { el.textContent = w.vantage; });
  }
  function setActiveTab(net) {
    var tn = document.getElementById("testnet-tab");
    var mn = document.getElementById("mainnet-tab");
    if (tn) { tn.classList.toggle("is-active", net === "testnet"); tn.setAttribute("aria-selected", net === "testnet" ? "true" : "false"); }
    if (mn) { mn.classList.toggle("is-active", net === "mainnet"); mn.setAttribute("aria-selected", net === "mainnet" ? "true" : "false"); }
    var note = document.getElementById("mainnet-preview-note");
    if (note) note.style.display = net === "mainnet" ? "" : "none";
    applyVantageWording(net);
  }
  function updateVantage(d) {
    var pill = document.querySelector(".geo-vantage-pill");
    if (!pill) return;
    var v = (d && (d.vantage || (d.vantage_point && d.vantage_point.label))) || "";
    pill.textContent = "Vantage: " + (v || "n/a");
  }
  function loadNetwork(net) {
    if (net === currentNet) return;
    currentNet = net;
    setActiveTab(net);
    fetch(FEEDS[net], { cache: "no-cache" })
      .then(function (r) { if (!r.ok) throw new Error(r.status); return r.json(); })
      .then(function (d) {
        updateVantage(d);
        renderCoverage(d); renderHeadline(d); renderSignals(d); renderDonut(d); renderLatency(d);
        renderTopCountries(d); renderProviderMini(d); renderMap(d);
        renderContinents(d); renderCountries(d); renderProviders(d);
        renderTrends(net);
      })
      .catch(function (e) { currentNet = null; fail("Could not load dataset (" + e.message + ")."); });
  }
  var _tnTab = document.getElementById("testnet-tab");
  var _mnTab = document.getElementById("mainnet-tab");
  if (_tnTab) _tnTab.addEventListener("click", function () { loadNetwork("testnet"); });
  if (_mnTab) _mnTab.addEventListener("click", function () { loadNetwork("mainnet"); });
  document.addEventListener("click", function () {
    Array.prototype.forEach.call(document.querySelectorAll(".info-wrap.open"), function (w) {
      w.classList.remove("open");
      var b = w.querySelector(".info-btn");
      if (b) b.setAttribute("aria-expanded", "false");
    });
    Array.prototype.forEach.call(document.querySelectorAll(".net-tab-wrap.open"), function (w) {
      w.classList.remove("open");
    });
  });

  loadNetwork(location.hash === "#testnet" ? "testnet" : "mainnet");
})();
