// Keep the optional viewer compressed until a contract is opened.
window.systemlensLoadAsyncApi = (() => {
  let pending;
  return () => {
    if (customElements.get("asyncapi-component")) return Promise.resolve();
    if (pending) return pending;
    pending = (async () => {
      if (typeof DecompressionStream !== "function") {
        throw new Error("Gzip decompression is unavailable");
      }
      const payload = document.getElementById("asyncapi-assets");
      const bytes = Uint8Array.from(atob(payload.textContent.trim()), char => char.charCodeAt(0));
      const stream = new Blob([bytes]).stream().pipeThrough(new DecompressionStream("gzip"));
      const assets = JSON.parse(await new Response(stream).text());
      window.systemlensAsyncApiCssImportPath = "data:text/css;base64," + btoa(assets.css);
      const script = document.createElement("script");
      script.textContent = assets.js;
      document.head.append(script);
      script.remove();
      if (!customElements.get("asyncapi-component")) {
        throw new Error("AsyncAPI component registration failed");
      }
      payload.remove();
    })().catch(error => {
      pending = undefined;
      throw error;
    });
    return pending;
  };
})();
