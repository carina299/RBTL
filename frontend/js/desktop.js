(function(){
  "use strict";

  const ICONS = {
    chat: '<path d="M4 5h16v11H8l-4 4z" fill="none" stroke="currentColor" stroke-width="1.9" stroke-linejoin="round"/><path d="M9 10.4c.6-1.4 3-1.4 3.4-.1.4-1.3 2.8-1.3 3.3.1.5 1.3-1.6 3-3.4 3.7-1.8-.7-3.8-2.4-3.3-3.7z" fill="currentColor" stroke="none"/>',

    memories: '<rect x="3.5" y="5" width="14" height="14" rx="2" fill="none" stroke="currentColor" stroke-width="1.9"/><circle cx="8" cy="9.5" r="1.4" fill="currentColor"/><path d="M4.5 17l4-4 2.5 2.3L15 11l4.5 6" fill="none" stroke="currentColor" stroke-width="1.7" stroke-linejoin="round"/>',

    shop: '<rect x="4" y="10" width="16" height="9" rx="1.5" fill="none" stroke="currentColor" stroke-width="1.8"/><path d="M4 10h16v3H4z" fill="none" stroke="currentColor" stroke-width="1.8"/><path d="M12 10v9M12 10c-1.6 0-3.4-1.1-3.4-2.7S9.6 4.8 11 5.4c1 .4 1 2.6 1 4.6zM12 10c1.6 0 3.4-1.1 3.4-2.7S14.4 4.8 13 5.4c-1 .4-1 2.6-1 4.6z" fill="none" stroke="currentColor" stroke-width="1.7" stroke-linejoin="round"/>',

    settings: '<circle cx="12" cy="12" r="2.7" fill="none" stroke="currentColor" stroke-width="1.8"/><path d="M12 4.4v2.1M12 17.5v2M19.5 12h-2.1M6.6 12h-2M17.4 6.6l-1.5 1.4M8 16l-1.4 1.4M17.4 17.4l-1.5-1.4M8 8L6.6 6.6" stroke="currentColor" stroke-width="1.8" stroke-linecap="round"/>',

    diary: '<path d="M6 4h9a2 2 0 0 1 2 2v13.5L14 17l-3 2.5V6a2 2 0 0 1-2-2H6z" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linejoin="round"/><path d="M8.5 9h5M8.5 12h4" stroke="currentColor" stroke-width="1.6" stroke-linecap="round"/>',

    home: '<path d="M4.5 11.5 12 5l7.5 6.5" fill="none" stroke="currentColor" stroke-width="1.9" stroke-linecap="round" stroke-linejoin="round"/><path d="M6.5 10.5V19h11v-8.5" fill="none" stroke="currentColor" stroke-width="1.9" stroke-linejoin="round"/><path d="M10 19v-5h4v5" fill="none" stroke="currentColor" stroke-width="1.7" stroke-linejoin="round"/>',

    playlist: '<path d="M9 17a2.2 2.2 0 1 1 0-4.4A2.2 2.2 0 0 1 9 17z" fill="none" stroke="currentColor" stroke-width="1.8"/><path d="M11.2 12.6V6l6-1.4v6" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"/><path d="M17.2 15.4a2.2 2.2 0 1 1 0-4.4 2.2 2.2 0 0 1 0 4.4z" fill="none" stroke="currentColor" stroke-width="1.8"/>',

    about: '<path d="M12 19s-6.2-3.8-8.2-7.6C2.4 8.6 3.6 5.6 6.9 5.6c1.8 0 2.8 1 3.3 1.9.5-.9 1.5-1.9 3.3-1.9 3.3 0 4.5 3 3.1 5.8C18.2 15.2 12 19 12 19z" fill="none" stroke="currentColor" stroke-width="1.7" stroke-linejoin="round"/><path d="M12 10.3v3M12 14.4h.01" stroke="currentColor" stroke-width="1.8" stroke-linecap="round"/>'
  };

  const APPS = [
    { id:"chat", name:"Chat", dock:true },
    { id:"memories", name:"Memories", dock:true },
    { id:"shop", name:"Shop", dock:true },
    { id:"settings", name:"Settings", dock:true },
    { id:"diary", name:"Diary", dock:false, blob:"blob1", tape:"tape-pink", rot:-7 },
    { id:"home", name:"Home", dock:false, blob:"blob2", tape:"tape-yellow", rot:5 },
    { id:"playlist", name:"Playlist", dock:false, blob:"blob3", tape:"tape-mint", rot:-4 },
    { id:"about", name:"About", dock:false, blob:"blob4", tape:"tape-pink", rot:6 }
  ];

  const gridApps = APPS.filter(function(app){
    return !app.dock;
  });

  const dockApps = APPS.filter(function(app){
    return app.dock;
  });

  const LAYOUTS = {
    mobile:[
      { left:24, top:35 },
      { left:74, top:35 },
      { left:30, top:62 },
      { left:76, top:64 }
    ],
    tablet:[
      { left:16, top:38 },
      { left:44, top:28 },
      { left:66, top:46 },
      { left:38, top:62 }
    ],
    desktop:[
      { left:9, top:24 },
      { left:17, top:42 },
      { left:7, top:58 },
      { left:15, top:76 }
    ]
  };

  const CARD_POS = {
    mobile:{
      left:"50%",
      top:"108px",
      right:"auto",
      width:"78%",
      transform:"translateX(-50%) rotate(-1deg)"
    },
    tablet:{
      left:"50%",
      top:"18%",
      right:"auto",
      width:"56%",
      transform:"translateX(-50%) rotate(-1deg)"
    },
    desktop:{
      left:"auto",
      top:"20%",
      right:"8%",
      width:"240px",
      transform:"rotate(1deg)"
    }
  };

  const STORAGE_KEY = "rbtl-desktop-icon-layout-v1";

  const iconLayer = document.getElementById("iconLayer");
  const dock = document.getElementById("dock");
  const toast = document.getElementById("toast");
  const lampToggle = document.getElementById("lampToggle");
  const editToggle = document.getElementById("editToggle");
  const partnerCard = document.getElementById("partnerCard");

  const mqMobile = window.matchMedia("(max-width: 640px)");
  const mqTablet = window.matchMedia("(min-width: 641px) and (max-width: 1024px)");

  let toastTimer = null;
  let currentBP = null;
  let editing = false;
  let dragged = null;
  let suppressClick = false;

  function showToast(text){
    toast.textContent = text;
    toast.classList.add("show");

    clearTimeout(toastTimer);
    toastTimer = setTimeout(function(){
      toast.classList.remove("show");
    }, 1600);
  }

  function getBreakpoint(){
    if (mqMobile.matches) return "mobile";
    if (mqTablet.matches) return "tablet";
    return "desktop";
  }

  function readSavedLayouts(){
    try{
      return JSON.parse(localStorage.getItem(STORAGE_KEY)) || {};
    }catch(error){
      return {};
    }
  }

  function writeSavedLayouts(data){
    localStorage.setItem(STORAGE_KEY, JSON.stringify(data));
  }

  function getSavedLayout(bp){
    const saved = readSavedLayouts();
    return saved[bp] || null;
  }

  function saveIconPosition(bp, id, left, top){
    const saved = readSavedLayouts();

    if (!saved[bp]){
      saved[bp] = {};
    }

    saved[bp][id] = {
      left:left,
      top:top
    };

    writeSavedLayouts(saved);
  }

  function clearCurrentLayout(){
    const saved = readSavedLayouts();

    if (saved[currentBP]){
      delete saved[currentBP];
      writeSavedLayouts(saved);
    }

    applyLayout(currentBP);
    showToast("layout reset");
  }

  function createIcons(){
    gridApps.forEach(function(app){
      const btn = document.createElement("button");
      btn.className = "app-icon";
      btn.style.setProperty("--r", app.rot + "deg");
      btn.dataset.id = app.id;
      btn.type = "button";

      btn.innerHTML =
        '<span class="sticker ' + app.blob + ' taped ' + app.tape + '">' +
          '<span class="spin">' +
            '<span class="tile">' +
              '<svg viewBox="0 0 24 24" aria-hidden="true">' + ICONS[app.id] + '</svg>' +
            '</span>' +
          '</span>' +
          '<span class="label">' + app.name + '</span>' +
        '</span>';

      btn.addEventListener("click", function(){
        if (suppressClick){
          suppressClick = false;
          return;
        }

        if (editing){
          showToast("drag me around");
          return;
        }

        showToast(app.name + " — coming soon");
      });

      btn.addEventListener("pointerdown", onIconPointerDown);

      iconLayer.appendChild(btn);
    });
  }

  function createDock(){
    const dockRot = [-3, 2, -2, 3];

    dockApps.forEach(function(app, i){
      const btn = document.createElement("button");
      btn.className = "dock-btn";
      btn.type = "button";
      btn.style.transform = "rotate(" + dockRot[i] + "deg)";
      btn.dataset.id = app.id;

      btn.innerHTML =
        '<span class="tile">' +
          '<svg viewBox="0 0 24 24" aria-hidden="true">' + ICONS[app.id] + '</svg>' +
        '</span>' +
        '<span class="label">' + app.name + '</span>';

      btn.addEventListener("click", function(){
        if (app.id === "memories") {
          window.location.href = "memories.html";
          return;
        }

        if (app.id === "chat") {
          window.location.href = "chat.html";
          return;
        }

        showToast(app.name + " — coming soon");
      });

      dock.appendChild(btn);
    });
  }

  function applyLayout(bp){
    document.body.classList.remove("mobile-layout", "tablet-layout", "desktop-layout");
    document.body.classList.add(bp + "-layout");

    const defaultCoords = LAYOUTS[bp];
    const saved = getSavedLayout(bp);

    document.querySelectorAll(".app-icon").forEach(function(el, i){
      const id = el.dataset.id;
      const savedPos = saved && saved[id];
      const pos = savedPos || defaultCoords[i];

      el.style.left = pos.left + "%";
      el.style.top = pos.top + "%";
    });

    const cp = CARD_POS[bp];

    partnerCard.style.left = cp.left;
    partnerCard.style.right = cp.right;
    partnerCard.style.top = cp.top;
    partnerCard.style.width = cp.width;
    partnerCard.style.transform = cp.transform;
  }

  function handleBreakpointChange(){
    const bp = getBreakpoint();

    if (bp !== currentBP){
      currentBP = bp;
      applyLayout(bp);
    }
  }

  function pad(n){
    return n < 10 ? "0" + n : "" + n;
  }

  const MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];
  const DAYS = ["Sun", "Mon", "Tue", "Wed", "Thu", "Fri", "Sat"];

  function updateClock(){
    const now = new Date();

    document.getElementById("clockTime").textContent = pad(now.getHours()) + ":" + pad(now.getMinutes());
    document.getElementById("clockDate").textContent = DAYS[now.getDay()] + ", " + MONTHS[now.getMonth()] + " " + now.getDate();

    const h = now.getHours();

    const msg = h < 6 ? "still up? were you thinking of me"
      : h < 11 ? "good morning — go get 'em today"
      : h < 14 ? "did you eat lunch yet? come tell me"
      : h < 18 ? "feeling sleepy? talk to me for a bit"
      : h < 23 ? "welcome back — i've been waiting"
      : "it's late, get some rest, i'm right here";

    document.getElementById("partnerMsg").textContent = msg;
  }

  function toggleDayNight(){
    document.body.classList.toggle("day");

    lampToggle.querySelector("span").textContent = document.body.classList.contains("day") ? "日" : "灯";

    lampToggle.classList.remove("zap");
    void lampToggle.offsetWidth;
    lampToggle.classList.add("zap");
  }

  function toggleEditing(){
    editing = !editing;

    document.body.classList.toggle("editing", editing);
    editToggle.classList.toggle("active", editing);
    editToggle.textContent = editing ? "done editing" : "edit desktop";

    showToast(editing ? "drag icons to decorate" : "layout saved");
  }

  function onIconPointerDown(event){
    if (!editing) return;

    const el = event.currentTarget;
    const rect = el.getBoundingClientRect();
    const boardRect = iconLayer.getBoundingClientRect();

    dragged = {
      el:el,
      boardRect:boardRect,
      offsetX:event.clientX - rect.left - rect.width / 2,
      offsetY:event.clientY - rect.top - rect.height / 2,
      startX:event.clientX,
      startY:event.clientY,
      moved:false
    };

    el.classList.add("dragging");
    el.setPointerCapture(event.pointerId);

    window.addEventListener("pointermove", onIconPointerMove);
    window.addEventListener("pointerup", onIconPointerUp, { once:true });
  }

  function onIconPointerMove(event){
    if (!dragged) return;

    const dx = event.clientX - dragged.startX;
    const dy = event.clientY - dragged.startY;

    if (Math.abs(dx) > 3 || Math.abs(dy) > 3){
      dragged.moved = true;
    }

    const x = event.clientX - dragged.boardRect.left - dragged.offsetX;
    const y = event.clientY - dragged.boardRect.top - dragged.offsetY;

    let left = x / dragged.boardRect.width * 100;
    let top = y / dragged.boardRect.height * 100;

    left = Math.max(6, Math.min(94, left));
    top = Math.max(12, Math.min(88, top));

    dragged.el.style.left = left + "%";
    dragged.el.style.top = top + "%";
  }

  function onIconPointerUp(){
    if (!dragged) return;

    window.removeEventListener("pointermove", onIconPointerMove);

    dragged.el.classList.remove("dragging");

    const left = parseFloat(dragged.el.style.left);
    const top = parseFloat(dragged.el.style.top);

    saveIconPosition(currentBP, dragged.el.dataset.id, left, top);

    if (dragged.moved){
      suppressClick = true;
    }

    dragged = null;
  }

  function initInteractiveDecorations(){
    document.querySelectorAll(".floating-heart").forEach(function(heart){
      heart.addEventListener("click", function(){
        heart.classList.remove("is-popped");
        void heart.offsetWidth;
        heart.classList.add("is-popped");
      });
    });
  }

  const CHAT_CONTACT_KEY = "rbtl-chat-contact-v1";

  function syncPartnerCard(){
    try{
      const saved = JSON.parse(localStorage.getItem(CHAT_CONTACT_KEY));
      if (!saved) return;

      const faceEl = document.getElementById("partnerFace");
      const nameEl = document.getElementById("partnerName");

      if (saved.avatar && faceEl){
        faceEl.textContent = saved.avatar;
      }

      if (saved.name && nameEl){
        nameEl.textContent = "FROM " + saved.name.toUpperCase();
      }
    }catch(error){
      /* keep defaults */
    }
  }

  function init(){
    createIcons();
    createDock();
    syncPartnerCard();

    currentBP = getBreakpoint();
    applyLayout(currentBP);

    updateClock();
    setInterval(updateClock, 1000 * 15);

    lampToggle.addEventListener("click", toggleDayNight);
    editToggle.addEventListener("click", toggleEditing);

    editToggle.addEventListener("dblclick", function(){
      clearCurrentLayout();
    });

    mqMobile.addEventListener("change", handleBreakpointChange);
    mqTablet.addEventListener("change", handleBreakpointChange);

    let resizeTimer = null;

    window.addEventListener("resize", function(){
      clearTimeout(resizeTimer);
      resizeTimer = setTimeout(handleBreakpointChange, 120);
    });

    initInteractiveDecorations();
  }

  document.addEventListener("DOMContentLoaded", init);
})();
