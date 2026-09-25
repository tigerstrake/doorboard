// @vitest-environment jsdom
import React from "react";
import { describe, it, expect, afterEach } from "vitest";
import { render, screen, cleanup } from "@testing-library/react";
import { AttributionNotice } from "./AttributionNotice";

/**
 * Attribution disclosure (ADR-0018 E-23).
 *
 * The regression: when attribution shipped, only the phone relay page disclosed it.
 * The on-wifi /visitor page and the doorpad attached names silently, which is
 * exactly what E-23 calls a review-blocking defect.
 */

afterEach(cleanup);

describe("AttributionNotice", () => {
  it("names the recognised person", () => {
    render(<AttributionNotice attributedTo="Tiger" />);
    expect(screen.getByTestId("attribution-notice")).toBeTruthy();
    expect(screen.getByText("Tiger")).toBeTruthy();
    expect(screen.getByRole("status")).toBeTruthy();
  });

  it("renders nothing when nothing will be attributed", () => {
    // The unrecognised case must be visually unchanged (E-25): no empty box, no
    // hint that the door was even looking.
    const { container } = render(<AttributionNotice attributedTo={null} />);
    expect(container.firstChild).toBeNull();
  });

  it("renders nothing for an empty name rather than an awkward blank", () => {
    const { container } = render(<AttributionNotice attributedTo="" />);
    expect(container.firstChild).toBeNull();
  });

  it("adapts the verb so a vote does not read as a note", () => {
    render(<AttributionNotice attributedTo="Tiger" verb="vote on" />);
    expect(screen.getByText(/vote on here/)).toBeTruthy();
  });

  it("tells the person how to opt out, not just that it is happening", () => {
    render(<AttributionNotice attributedTo="Tiger" />);
    expect(screen.getByText(/remove it, or to stop recognising you/i)).toBeTruthy();
  });

  it("renders a hostile display name inertly", () => {
    const hostile = "<script>alert('xss')</script>";
    render(<AttributionNotice attributedTo={hostile} />);
    expect(screen.getByText(hostile)).toBeTruthy();
    expect(document.querySelector("script")).toBeNull();
  });

  it("says the same three things in one line when compact", () => {
    // The composers on the 600 px panel: the full notice pushed Submit below the fold on
    // a six-option poll. Compact keeps E-23 whole — who, that the write carries it, and
    // the way out — in a single paragraph.
    render(<AttributionNotice attributedTo="Tiger" verb="vote on" compact />);
    const notice = screen.getByTestId("attribution-notice");
    expect(notice.classList.contains("db-attribution-notice--compact")).toBe(true);
    expect(notice.querySelectorAll("p")).toHaveLength(1);
    expect(screen.getByText("Tiger")).toBeTruthy();
    expect(screen.getByText(/vote on here/)).toBeTruthy();
    expect(screen.getByText(/Ask the household admin to remove it/)).toBeTruthy();
    expect(screen.getByRole("status")).toBeTruthy();
  });
});
