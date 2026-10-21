const path = require("path");
const fs = require("fs");
const bridge = require("./lib/chrome-recorder-bridge");

const EDGE_CANDIDATES = [
  process.env.EDGE_PATH,
  "C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe",
  "C:/Program Files/Microsoft/Edge/Application/msedge.exe",
].filter(Boolean);

function findEdge() {
  for (const p of EDGE_CANDIDATES) {
    if (fs.existsSync(p)) return p;
  }
  throw new Error("Edge not found. Set EDGE_PATH or install Edge.");
}

function findPuppeteer() {
  try { return require("puppeteer-core"); } catch {}
  const dirs = [
    process.env.PUPPETEER_MODULE_PATH,
    "D:/gitAll/auto-deploy/node_modules/puppeteer-core",
    path.resolve(__dirname, "../node_modules/puppeteer-core"),
  ].filter(Boolean);
  for (const d of dirs) {
    try { return require(d); } catch {}
  }
  throw new Error("puppeteer-core not found. npm install puppeteer-core or set PUPPETEER_MODULE_PATH.");
}

const DEMO_HTML = "<!DOCTYPE html><html><head><title>openeyes-record-demo</title></head>" +
  "<body>" +
  "<button id=\"btn-a\">Button A</button>" +
  "<button id=\"btn-b\">Button B</button>" +
  "<input id=\"fld\" type=\"text\"/>" +
  "<button id=\"btn-d\">Button D</button>" +
  "</body></html>";

async function main() {
  const oi = process.argv.indexOf("--out");
  const outPath = oi >= 0
    ? path.resolve(process.argv[oi + 1])
    : path.resolve(__dirname, "record-out/trajectory-edge-example.jsonl");
  const executablePath = findEdge();
  const puppeteer = findPuppeteer();
  const tmpProfile = path.resolve(__dirname, ".edge-record-demo-profile");
  fs.mkdirSync(tmpProfile, { recursive: true });
  console.log("Edge:", executablePath);
  console.log("Output:", outPath);

  const browser = await puppeteer.launch({
    executablePath,
    headless: "new",
    userDataDir: tmpProfile,
    args: ["--no-first-run", "--disable-extensions", "--disable-gpu"],
  });

  try {
    const pages = await browser.pages();
    const page = pages[0] || (await browser.newPage());
    const rec = await bridge.recordPage(page, {
      name: "edge-record-demo",
      appName: "Edge",
      description: "Reproducible 5-step Edge recording example",
      outputPath: outPath,
      idleTimeoutMs: 3000,
      maxEvents: 20,
    });

    await page.goto("data:text/html," + encodeURIComponent(DEMO_HTML));
    await page.waitForSelector("#btn-a");

    await page.click("#btn-a");
    await page.click("#btn-b");
    await page.type("#fld", "hello");
    await page.click("#btn-d");
    await page.click("#btn-a");

    const result = await rec.promise;
    console.log("Recorded steps:", result.steps.filter((s) => !s._kind).length, "stopReason:", result.stopReason);
    if (!fs.existsSync(outPath)) throw new Error("Output file not written.");
    const { header, steps } = bridge.parseJSONL(fs.readFileSync(outPath, "utf-8"));
    console.log("Header:", JSON.stringify({ id: header.id, step_count: header.step_count }));
    console.log("Actions:", steps.map((s) => s.action).join(","));
    if (steps.length === 0) throw new Error("No steps recorded.");
    console.log("DONE. Trajectory JSONL written. Run python -c to verify:");
    console.log("  python -c \"from openeyes.record import Trajectory; t=Trajectory.load(r'" + outPath + "'); print(len(t.steps), 'steps loaded OK')\"");
  } finally {
    await browser.close();
  }
}

main().catch((err) => {
  console.error(err.message || err);
  process.exit(1);
});