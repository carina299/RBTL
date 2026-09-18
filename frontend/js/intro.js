(function(){
  "use strict";

  const intro = document.getElementById("intro");
  const stars = document.getElementById("stars");
  const introLamp = document.getElementById("introLamp");
  const doorBtn = document.getElementById("doorBtn");
  const skipBtn = document.getElementById("skipBtn");
  const flood = document.getElementById("flood");

  const reduceMotion = window.matchMedia("(prefers-reduced-motion: reduce)").matches;

  let entering = false;

  function makeStars(container, n, spread){
    if (!container) return;

    for (let i = 0; i < n; i++){
      const s = document.createElement("span");
      s.style.left = Math.random() * 100 + "%";
      s.style.top = Math.random() * spread + "%";
      s.style.opacity = (0.2 + Math.random() * 0.5).toFixed(2);
      s.style.animationDelay = Math.random() * 3.2 + "s";
      container.appendChild(s);
    }
  }

  function goToDesktop(){
    window.location.href = "desktop.html";
  }

  function openDoor(){
    if (entering) return;
    entering = true;

    doorBtn.classList.add("open");
    doorBtn.setAttribute("disabled", "true");

    if (reduceMotion){
      intro.classList.add("fading");
      setTimeout(goToDesktop, 220);
      return;
    }

    setTimeout(function(){
      flood.classList.add("run");
      intro.classList.add("fading");
    }, 500);

    setTimeout(goToDesktop, 1150);
  }

  makeStars(stars, 46, 70);

  if (introLamp){
    introLamp.addEventListener("click", function(){
      intro.classList.toggle("lit");
    });
  }

  if (doorBtn){
    doorBtn.addEventListener("click", openDoor);
  }

  if (skipBtn){
    skipBtn.addEventListener("click", function(){
      if (entering) return;
      entering = true;

      doorBtn.classList.add("open");
      intro.classList.add("fading");

      setTimeout(goToDesktop, reduceMotion ? 80 : 280);
    });
  }

  setTimeout(function(){
    if (!entering){
      skipBtn.classList.add("show");
    }
  }, 3500);
})();
