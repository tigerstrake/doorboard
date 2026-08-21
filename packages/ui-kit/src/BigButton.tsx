import React from "react";

export interface BigButtonProps {
  onClick?: (event: React.MouseEvent<HTMLButtonElement>) => void;
  icon?: React.ReactNode;
  children?: React.ReactNode;
  variant?: "primary" | "secondary";
  disabled?: boolean;
  className?: string;
  id?: string;
  type?: "button" | "submit" | "reset";
  /**
   * One short line saying what happens when this is pressed.
   *
   * The doorpad's tiles were labelled with what they *are* ("Guestbook", "Poll Vote")
   * rather than what they do, which reads fine to whoever built the door and tells a
   * visitor standing in a hallway nothing. It is rendered inside the button and marked
   * `aria-hidden`, because the accessible name already carries the label and a
   * screen reader should not read a decorative gloss twice.
   */
  hint?: string;
}

export function BigButton({
  onClick,
  icon,
  children,
  variant = "secondary",
  disabled = false,
  className = "",
  id,
  type = "button",
  hint,
}: BigButtonProps) {
  return (
    <button
      id={id}
      type={type}
      onClick={onClick}
      disabled={disabled}
      className={`db-big-button db-big-button--${variant} ${hint ? "db-big-button--with-hint" : ""} ${className}`}
      data-testid="big-button"
    >
      {icon && <span className="db-big-button__icon">{icon}</span>}
      {children && (
        <span className="db-big-button__body">
          <span className="db-big-button__text">{children}</span>
          {hint && (
            <span className="db-big-button__hint" aria-hidden="true">
              {hint}
            </span>
          )}
        </span>
      )}
    </button>
  );
}
