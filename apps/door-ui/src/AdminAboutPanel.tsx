import React from "react";
import { aboutFixture } from "./fixtures";

// "About this project" admin panel (T-608): a fuller writeup than the wallboard
// tile, plus the full fun-facts breakdown. All facts are build-time and public
// (project structure + line counts) — no private or diagnostic data.

const COUNT_LABELS: Array<{ key: keyof typeof aboutFixture.stats.counts; label: string }> = [
  { key: "services", label: "Services" },
  { key: "packages", label: "Shared packages" },
  { key: "integrations", label: "Integrations" },
  { key: "contract_event_types", label: "Contract event types" },
  { key: "adrs", label: "Architecture decisions (ADRs)" },
  { key: "task_briefs", label: "Task briefs" },
  { key: "milestones", label: "Milestones (M0–M7)" },
];

export function AdminAboutPanel() {
  const { name, tagline, description, stats } = aboutFixture;

  return (
    <section className="admin-about-panel" data-testid="admin-about-panel">
      <h2>About {name}</h2>
      <p className="about-tagline">{tagline}</p>
      <p>{description}</p>

      <h3>Fun facts</h3>
      <p>
        <strong>{stats.lines_of_code.toLocaleString()}</strong> lines of code across{" "}
        <strong>{stats.tracked_files.toLocaleString()}</strong> tracked files.
      </p>

      <table className="about-lang-table">
        <thead>
          <tr>
            <th>Language</th>
            <th>Files</th>
            <th>Lines</th>
          </tr>
        </thead>
        <tbody>
          {stats.languages.map((lang) => (
            <tr key={lang.name}>
              <td>{lang.name}</td>
              <td>{lang.files.toLocaleString()}</td>
              <td>{lang.lines.toLocaleString()}</td>
            </tr>
          ))}
        </tbody>
      </table>

      <dl className="about-counts">
        {COUNT_LABELS.map(({ key, label }) => (
          <div className="about-count-row" key={key}>
            <dt>{label}</dt>
            <dd>{stats.counts[key].toLocaleString()}</dd>
          </div>
        ))}
      </dl>

      <p className="about-asof">
        Stats generated {stats.generated_at} — refresh with{" "}
        <code>python tools/project-stats/collect.py</code>.
      </p>
    </section>
  );
}
