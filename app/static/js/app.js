/* ═══════════════════════════════════════════════════════════════════════════
   QR Reviews — App JavaScript
   Star rating, clipboard, chips, toast, typewriter, Razorpay, HTMX hooks
   ═══════════════════════════════════════════════════════════════════════════ */

(function () {
  "use strict";

  /* ────────────────────────────────────────────────────────────────────────
     Toast Notifications
     ──────────────────────────────────────────────────────────────────────── */

  const toastEl = document.getElementById("toast");
  let toastTimer = null;

  window.showToast = function (msg, type, duration) {
    if (!toastEl) return;
    type = type || "";
    duration = duration || 2600;
    toastEl.textContent = msg;
    toastEl.className = "toast show" + (type ? " toast-" + type : "");
    clearTimeout(toastTimer);
    toastTimer = setTimeout(function () {
      toastEl.classList.remove("show");
    }, duration);
  };


  /* ────────────────────────────────────────────────────────────────────────
     Star Rating Component
     ──────────────────────────────────────────────────────────────────────── */

  const STAR_PATH = "M12 2l2.9 6.6L22 9.3l-5.2 4.9L18.2 22 12 18.3 5.8 22l1.4-7.8L2 9.3l7.1-.7L12 2z";
  const RATING_LABELS = {
    1: "Rough visit",
    2: "Not great",
    3: "It was okay",
    4: "Really good",
    5: "Loved it",
  };

  function initStarRating() {
    var container = document.getElementById("stars");
    if (!container) return;

    var ratingInput = document.getElementById("rating-value");
    var ratingLabel = document.getElementById("ratingLabel");
    var stamp = document.getElementById("rateStamp");
    var continueBtn = document.getElementById("toNotesBtn");

    // Build star buttons
    for (var i = 1; i <= 5; i++) {
      var btn = document.createElement("button");
      btn.className = "star-btn";
      btn.type = "button";
      btn.setAttribute("aria-label", i + " star");
      btn.dataset.value = i;
      btn.innerHTML =
        '<svg viewBox="0 0 24 24"><path d="' + STAR_PATH + '"/></svg>';
      btn.addEventListener("click", handleStarClick);
      container.appendChild(btn);
    }

    function handleStarClick(e) {
      var btn = e.currentTarget;
      var val = parseInt(btn.dataset.value, 10);
      setRating(val);
    }

    function setRating(r) {
      // Paint stars
      var btns = container.querySelectorAll(".star-btn");
      btns.forEach(function (b, idx) {
        b.classList.toggle("filled", idx < r);
      });

      // Update hidden input
      if (ratingInput) ratingInput.value = r;

      // Update label
      if (ratingLabel) ratingLabel.textContent = RATING_LABELS[r] || "";

      // Show stamp
      if (stamp) {
        var stars = "";
        for (var j = 0; j < 5; j++) stars += j < r ? "★" : "☆";
        stamp.textContent = stars + " noted";
        stamp.classList.remove("show");
        void stamp.offsetWidth; // force reflow
        stamp.classList.add("show");
      }

      // Enable continue
      if (continueBtn) continueBtn.disabled = false;

      // Build chips for this rating
      buildChips(r);
    }
  }


  /* ────────────────────────────────────────────────────────────────────────
     Chip Suggestions
     ──────────────────────────────────────────────────────────────────────── */

  var CHIP_SETS = {
    low:  ["Slow service", "Cold drink", "Wrong order", "Not clean", "Rude staff"],
    mid:  ["Good coffee", "A bit slow", "Nothing special", "Friendly staff"],
    high: ["Great coffee", "Friendly staff", "Cozy space", "Fast service", "Would recommend"],
  };

  function chipTier(r) {
    return r <= 2 ? "low" : r === 3 ? "mid" : "high";
  }

  function buildChips(rating) {
    var wrap = document.getElementById("chips");
    if (!wrap) return;

    var tier = chipTier(rating);
    var labels = CHIP_SETS[tier] || CHIP_SETS.high;
    var chipsInput = document.getElementById("chips-value");

    wrap.innerHTML = "";
    labels.forEach(function (label) {
      var c = document.createElement("button");
      c.className = "chip";
      c.type = "button";
      c.textContent = label;
      c.addEventListener("click", function () {
        c.classList.toggle("selected");
        updateChipsInput();
      });
      wrap.appendChild(c);
    });

    // Update hint
    var hint = document.getElementById("notesHint");
    if (hint) {
      if (rating <= 3) {
        hint.innerHTML =
          "<b>Be as honest as you like</b> — the business wants to hear it. You\u2019ll still be able to post this, and send a private note too.";
      } else {
        hint.innerHTML =
          "This becomes the basis of your review \u2014 feel free to add specifics.";
      }
    }

    function updateChipsInput() {
      if (!chipsInput) return;
      var selected = [];
      wrap.querySelectorAll(".chip.selected").forEach(function (ch) {
        selected.push(ch.textContent);
      });
      chipsInput.value = selected.join(", ");
    }
  }

  // Also init chips from server-side (when feedback partial loads)
  function initChips() {
    var wrap = document.getElementById("chips");
    if (!wrap) return;
    wrap.querySelectorAll(".chip").forEach(function (c) {
      c.addEventListener("click", function () {
        c.classList.toggle("selected");
        var chipsInput = document.getElementById("chips-value");
        if (chipsInput) {
          var selected = [];
          wrap.querySelectorAll(".chip.selected").forEach(function (ch) {
            selected.push(ch.textContent);
          });
          chipsInput.value = selected.join(", ");
        }
      });
    });
  }


  /* ────────────────────────────────────────────────────────────────────────
     Clipboard Copy
     ──────────────────────────────────────────────────────────────────────── */

  window.copyReview = function () {
    var ta = document.getElementById("reviewText");
    if (!ta) return;
    var text = ta.value;

    if (navigator.clipboard && navigator.clipboard.writeText) {
      navigator.clipboard.writeText(text).then(function () {
        showCopiedFeedback();
      }).catch(function () {
        fallbackCopy(text);
      });
    } else {
      fallbackCopy(text);
    }
  };

  function fallbackCopy(text) {
    var tmp = document.createElement("textarea");
    tmp.value = text;
    tmp.style.position = "fixed";
    tmp.style.opacity = "0";
    document.body.appendChild(tmp);
    tmp.focus();
    tmp.select();
    try { document.execCommand("copy"); showCopiedFeedback(); } catch (e) { /* noop */ }
    document.body.removeChild(tmp);
  }

  function showCopiedFeedback() {
    var tag = document.getElementById("copiedTag");
    if (tag) {
      tag.classList.add("show");
      setTimeout(function () { tag.classList.remove("show"); }, 1800);
    }
    showToast("Review copied to clipboard", "success");

    // Fire beacon to track copy
    var reviewId = document.getElementById("review-id");
    var slug = document.getElementById("biz-slug");
    if (reviewId && slug) {
      var finalText = document.getElementById("reviewText");
      fetch("/review/" + slug.value + "/copied", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          review_id: reviewId.value,
          final_text: finalText ? finalText.value : null,
        }),
      }).catch(function () { /* best-effort */ });
    }
  }


  /* ────────────────────────────────────────────────────────────────────────
     Post on Google Redirect
     ──────────────────────────────────────────────────────────────────────── */

  window.postOnGoogle = function (googleUrl) {
    // Fire beacon
    var reviewId = document.getElementById("review-id");
    var slug = document.getElementById("biz-slug");
    if (reviewId && slug) {
      fetch("/review/" + slug.value + "/redirected", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ review_id: reviewId.value }),
      }).catch(function () { /* best-effort */ });
    }

    // Open Google in new tab
    if (googleUrl) {
      window.open(googleUrl, "_blank");
    } else {
      showToast("Google Review link not set for this business", "error");
    }
  };


  /* ────────────────────────────────────────────────────────────────────────
     Typewriter Effect (for AI-generated review)
     ──────────────────────────────────────────────────────────────────────── */

  window.typewriterEffect = function (elementId, text, speed) {
    var el = document.getElementById(elementId);
    if (!el) return;

    speed = speed || 14;
    el.value = "";
    el.readOnly = true;
    var i = 0;

    var interval = setInterval(function () {
      el.value = text.slice(0, i);
      i++;
      if (i > text.length) {
        clearInterval(interval);
        el.readOnly = false;
        el.focus();
        el.setSelectionRange(text.length, text.length);
      }
    }, speed);
  };


  /* ────────────────────────────────────────────────────────────────────────
     Razorpay Checkout
     ──────────────────────────────────────────────────────────────────────── */

  window.initRazorpayCheckout = function (options) {
    /*
      options = {
        key: "rzp_test_...",
        order_id: "order_...",
        amount: 149900,
        currency: "INR",
        name: "revQR",
        description: "QR Code Generation",
        business_name: "Kaffi & Co.",
        email: "biz@example.com",
        slug: "kaffi-co-a7x",
        csrf_token: "...",
      }
    */
    var rzpOptions = {
      key: options.key,
      amount: options.amount,
      currency: options.currency || "INR",
      name: options.name || "revQR",
      description: options.description || "QR Code for " + options.business_name,

      order_id: options.order_id,
      prefill: {
        email: options.email || "",
      },
      theme: {
        color: getComputedStyle(document.documentElement)
          .getPropertyValue("--accent")
          .trim() || "#3A5A78",
      },
      handler: function (response) {
        // Payment succeeded — verify on server
        var form = document.createElement("form");
        form.method = "POST";
        form.action = "/dashboard/qr/verify-payment";

        var fields = {
          razorpay_order_id: response.razorpay_order_id,
          razorpay_payment_id: response.razorpay_payment_id,
          razorpay_signature: response.razorpay_signature,
        };
        if (options.csrf_token) {
          fields.csrf_token = options.csrf_token;
        }

        Object.keys(fields).forEach(function (name) {
          var input = document.createElement("input");
          input.type = "hidden";
          input.name = name;
          input.value = fields[name];
          form.appendChild(input);
        });

        document.body.appendChild(form);
        form.submit();
      },
      modal: {
        ondismiss: function () {
          showToast("Payment cancelled", "error");
        },
      },
    };

    var rzp = new Razorpay(rzpOptions);
    rzp.open();
  };

  window.startPayment = function () {
    var payBtn = document.getElementById("payBtn");
    if (payBtn) {
      payBtn.disabled = true;
      payBtn.textContent = "PROCESSING...";
    }

    fetch("/dashboard/qr/create-order", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
    })
      .then(function (res) { return res.json(); })
      .then(function (data) {
        if (data.error) {
          showToast(data.error, "error");
          if (payBtn) { payBtn.disabled = false; payBtn.textContent = "PAY & GENERATE QR"; }
          return;
        }
        initRazorpayCheckout(data);
      })
      .catch(function (err) {
        showToast("Something went wrong. Please try again.", "error");
        if (payBtn) { payBtn.disabled = false; payBtn.textContent = "PAY & GENERATE QR"; }
      });
  };


  /* ────────────────────────────────────────────────────────────────────────
     Dashboard: Mobile Nav Toggle
     ──────────────────────────────────────────────────────────────────────── */

  function initMobileNav() {
    var toggle = document.getElementById("dashMobileToggle");
    var menu = document.getElementById("dashMobileMenu");
    if (!toggle || !menu) return;

    toggle.addEventListener("click", function () {
      menu.classList.toggle("open");
    });

    // Close on backdrop click
    menu.addEventListener("click", function (e) {
      if (e.target === menu) {
        menu.classList.remove("open");
      }
    });
  }


  /* ────────────────────────────────────────────────────────────────────────
     Dashboard: Color Picker
     ──────────────────────────────────────────────────────────────────────── */

  function initColorPicker() {
    var input = document.getElementById("colorInput");
    var preview = document.getElementById("colorPreview");
    var hex = document.getElementById("colorHex");
    if (!input) return;

    input.addEventListener("input", function () {
      var val = input.value;
      if (preview) preview.style.background = val;
      if (hex) hex.textContent = val;
      // Live preview brand color
      document.documentElement.style.setProperty("--accent", val);
    });

    if (preview) {
      preview.addEventListener("click", function () {
        input.click();
      });
    }
  }


  /* ────────────────────────────────────────────────────────────────────────
     Dashboard: Logo Upload Preview
     ──────────────────────────────────────────────────────────────────────── */

  function initLogoUpload() {
    var fileInput = document.getElementById("logoFile");
    var uploadArea = document.getElementById("uploadArea");
    var previewImg = document.getElementById("logoPreview");
    if (!fileInput) return;

    if (uploadArea) {
      uploadArea.addEventListener("click", function () {
        fileInput.click();
      });

      uploadArea.addEventListener("dragover", function (e) {
        e.preventDefault();
        uploadArea.style.borderColor = "var(--accent)";
      });
      uploadArea.addEventListener("dragleave", function () {
        uploadArea.style.borderColor = "";
      });
      uploadArea.addEventListener("drop", function (e) {
        e.preventDefault();
        uploadArea.style.borderColor = "";
        if (e.dataTransfer.files.length) {
          fileInput.files = e.dataTransfer.files;
          showPreview(e.dataTransfer.files[0]);
        }
      });
    }

    fileInput.addEventListener("change", function () {
      if (fileInput.files.length) {
        showPreview(fileInput.files[0]);
      }
    });

    function showPreview(file) {
      if (!previewImg) return;
      var reader = new FileReader();
      reader.onload = function (e) {
        previewImg.src = e.target.result;
        previewImg.style.display = "block";
      };
      reader.readAsDataURL(file);
    }
  }


  /* ────────────────────────────────────────────────────────────────────────
     Password Toggle
     ──────────────────────────────────────────────────────────────────────── */

  function initPasswordToggles() {
    document.querySelectorAll(".input-toggle").forEach(function (btn) {
      btn.addEventListener("click", function () {
        var input = btn.parentElement.querySelector("input");
        if (!input) return;
        if (input.type === "password") {
          input.type = "text";
          btn.textContent = "⚆";
          btn.setAttribute("aria-label", "Hide password");
        } else {
          input.type = "password";
          btn.textContent = "⊙";
          btn.setAttribute("aria-label", "Show password");
        }
      });
    });
  }


  /* ────────────────────────────────────────────────────────────────────────
     HTMX Event Hooks
     ──────────────────────────────────────────────────────────────────────── */

  document.addEventListener("htmx:afterSwap", function (e) {
    // Re-init components after HTMX swaps content
    initChips();
    initPasswordToggles();

    // If the review text was swapped in, run typewriter
    var reviewText = document.getElementById("reviewText");
    if (reviewText && reviewText.dataset.typewriter === "true") {
      var text = reviewText.dataset.text || reviewText.value;
      typewriterEffect("reviewText", text);
    }
  });

  document.addEventListener("htmx:beforeRequest", function () {
    // Could add global loading indicator here
  });

  document.addEventListener("htmx:responseError", function () {
    showToast("Something went wrong. Please try again.", "error");
  });


  /* ────────────────────────────────────────────────────────────────────────
     Review Variation Switching
     ──────────────────────────────────────────────────────────────────────── */

  window.selectVariation = function (style) {
    var dataEl = document.getElementById("variations-data");
    if (!dataEl) return;

    try {
      var variations = JSON.parse(dataEl.value);
      var text = variations[style] || "";

      // Update textarea
      var ta = document.getElementById("reviewText");
      if (ta) ta.value = text;

      // Update active tab
      document.querySelectorAll(".variation-tab").forEach(function (tab) {
        tab.classList.toggle("active", tab.dataset.style === style);
      });
    } catch (e) {
      // JSON parse error — fallback silently
    }
  };


  /* ────────────────────────────────────────────────────────────────────────
     Smart Enter Key & Next-Field Navigation
     ──────────────────────────────────────────────────────────────────────── */

  function initSmartFormNavigation() {
    function setupEnterKeyHints() {
      // Find all forms or input containers
      var containers = document.querySelectorAll("form, .wa-quick-send, .wa-source-builder");
      containers.forEach(function (container) {
        var inputs = Array.prototype.filter.call(
          container.querySelectorAll("input:not([type=hidden]):not([type=checkbox]):not([type=radio]):not([type=submit]):not([disabled]), select:not([disabled])"),
          function (el) {
            return el.offsetParent !== null;
          }
        );
        inputs.forEach(function (inp, idx) {
          if (idx < inputs.length - 1) {
            inp.setAttribute("enterkeyhint", "next");
          } else {
            inp.setAttribute("enterkeyhint", "go");
          }
        });
      });
    }

    setupEnterKeyHints();

    document.addEventListener("keydown", function (e) {
      if (e.key !== "Enter") return;

      var target = e.target;
      if (!target) return;

      // In textareas:
      // Regular Enter: allows normal newline
      // Ctrl+Enter or Cmd+Enter: submits the parent form if available
      if (target.tagName === "TEXTAREA") {
        if (e.ctrlKey || e.metaKey) {
          var parentForm = target.closest("form");
          if (parentForm) {
            var submitBtn = parentForm.querySelector("button[type=submit], input[type=submit]");
            if (submitBtn) {
              e.preventDefault();
              submitBtn.click();
            }
          }
        }
        return;
      }

      // Ignore buttons, links, or file inputs
      if (target.tagName === "BUTTON" || target.type === "submit" || target.type === "file") {
        return;
      }

      if (target.tagName === "INPUT" || target.tagName === "SELECT") {
        var container = target.closest("form") || target.closest(".wa-quick-send") || target.closest(".wa-source-builder") || target.closest(".source-attribution") || target.closest(".standee-controls") || target.closest("main") || document.body;
        if (!container) return;

        var focusable = Array.prototype.filter.call(
          container.querySelectorAll("input:not([type=hidden]):not([type=checkbox]):not([type=radio]):not([type=submit]):not([disabled]), select:not([disabled]), textarea:not([disabled])"),
          function (el) {
            return el.offsetParent !== null;
          }
        );

        var idx = focusable.indexOf(target);
        if (idx > -1 && idx < focusable.length - 1) {
          e.preventDefault();
          var nextEl = focusable[idx + 1];
          nextEl.focus();
          if (nextEl.select && typeof nextEl.select === "function" && nextEl.type !== "color") {
            nextEl.select();
          }
        } else if (idx === focusable.length - 1) {
          // If it's a form and last input, submit form
          if (container.tagName === "FORM" || container.querySelector("form")) {
            var submitBtn = container.querySelector("button[type=submit], input[type=submit]");
            if (submitBtn) {
              e.preventDefault();
              submitBtn.click();
            }
          }
        }
      }
    });
  }



  /* ────────────────────────────────────────────────────────────────────────
     Init on DOM Ready
     ──────────────────────────────────────────────────────────────────────── */

  document.addEventListener("DOMContentLoaded", function () {
    initStarRating();
    initChips();
    initMobileNav();
    initColorPicker();
    initLogoUpload();
    initPasswordToggles();
    initSmartFormNavigation();
  });

  // Re-init after HTMX partial swap (e.g., review flow partials)
  document.addEventListener("htmx:afterSettle", function () {
    initChips();
    initSmartFormNavigation();
  });

})();


