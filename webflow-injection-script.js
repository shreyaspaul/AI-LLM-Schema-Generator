// Webflow Site Settings Head Injection Script
// This script fetches manifest.v1.txt (JSON content in .txt file) and injects schema markup for the current page
// Note: Webflow doesn't allow .json uploads, so we use .txt file containing JSON

(function () {
  var MANIFEST_URL = "https://your-webflow-asset-url/manifest.v1.txt"; // ← replace with your Webflow asset URL (.txt file)

  function currentPath() {
    // Prefer canonical link if present (handles redirects/aliases)
    var link = document.querySelector('link[rel="canonical"]');
    if (link && link.href) {
      try { 
        return new URL(link.href).pathname.replace(/\/+$/,'') || '/'; 
      } catch(e){}
    }
    // Fallback to current location pathname
    var p = location.pathname.replace(/\/+$/,'');
    return p === "" ? "/" : p;
  }

  function inject(json) {
    try {
      var s = document.createElement('script');
      s.type = 'application/ld+json';
      s.text = JSON.stringify(json);
      (document.head || document.documentElement).appendChild(s);
    } catch(e) {}
  }

  fetch(MANIFEST_URL, { cache: "no-cache" })
    .then(function(r){ return r.text(); }) // Get as text first
    .then(function(text){
      try {
        var manifest = JSON.parse(text); // Parse JSON from text file
        var path = currentPath();
        // Try multiple path variations for matching (handles trailing slash differences)
        var payload = manifest[path]
                   || manifest[path.replace(/\/$/,'')]
                   || manifest[(path + '/').replace(/\/+$/,'/')];
        if (payload) inject(payload);
      } catch(e) {
        // Silently fail if JSON parsing fails
        console.error('Failed to parse manifest:', e);
      }
    })
    .catch(function(e){
      // Silently fail if fetch fails
      console.error('Failed to fetch manifest:', e);
    });
})();

