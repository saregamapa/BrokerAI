/**
 * Thin wrappers matching the auth API (plain JS — no build step).
 * Requires /static/js/app.js (BrokerAI global).
 */
(function () {
  "use strict";

  function _B() {
    var B = window.BrokerAI;
    if (!B) throw new Error("BrokerAI is not loaded");
    return B;
  }

  window.BrokerAIAuthService = {
    signup: function (name, email, password, planId) {
      return _B().apiJson("/api/auth/signup", {
        method: "POST",
        body: JSON.stringify({ name: name, email: email, password: password, planId: planId }),
      });
    },
    login: function (email, password) {
      return _B().apiJson("/api/auth/login", {
        method: "POST",
        body: JSON.stringify({ email: email, password: password }),
      });
    },
    logout: function () {
      _B().logout();
    },
    me: function () {
      return _B().apiJson("/api/auth/me", { method: "GET" });
    },
  };
})();
