// Sphinx has no built-in idea of GitHub's blockquote alerts
// ('> [!NOTE]', '> [!TIP]', '> [!IMPORTANT]', '> [!WARNING]', '> [!CAUTION]'
// -- see https://docs.github.com/en/get-started/writing-on-github/getting-started-with-writing-and-formatting-on-github/basic-writing-and-formatting-syntax#alerts).
// GitHub recognizes and styles the marker itself; MyST/docutils just emit an
// ordinary <blockquote> with the literal "[!NOTE]" text as the start of its
// first paragraph. This finds that marker, strips it, and turns the
// blockquote into the same kind of colored, labeled box GitHub shows --
// styling is in custom.css's "GitHub's blockquote-based alerts" section.
document.addEventListener("DOMContentLoaded", function () {
  var marker = /^\[!(NOTE|TIP|IMPORTANT|WARNING|CAUTION)\]\s*/;
  // same icons GitHub itself uses for each alert type.
  var emoji = {
    note: "ℹ️",
    tip: "💡",
    important: "❗",
    warning: "⚠️",
    caution: "🛑",
  };

  document.querySelectorAll(".rst-content blockquote").forEach(function (blockquote) {
    var firstParagraph = blockquote.querySelector("p");
    var firstNode = firstParagraph && firstParagraph.firstChild;
    if (!firstNode || firstNode.nodeType !== Node.TEXT_NODE) {
      return;
    }

    var match = marker.exec(firstNode.textContent);
    if (!match) {
      return;
    }

    firstNode.textContent = firstNode.textContent.slice(match[0].length);

    var type = match[1].toLowerCase();
    blockquote.classList.add("alert", "alert-" + type);

    var title = document.createElement("span");
    title.className = "alert-title";
    title.textContent = emoji[type] + " " + type;
    firstParagraph.insertBefore(title, firstParagraph.firstChild);
    firstParagraph.insertBefore(document.createElement("br"), title.nextSibling);
  });
});
