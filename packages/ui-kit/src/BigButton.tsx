import React from "react";

export interface BigButtonProps {
  onClick?: (event: React.MouseEvent<HTMLButtonElement>) => void;
  icon?: React.ReactNode;
  children?: React.ReactNode;
  variant?: "primary" | "secondary";
  disabled?: boolean;
  className?: string;
  id?: string;
}

export function BigButton({
  onClick,
  icon,
  children,
  variant = "secondary",
  disabled = false,
  className = "",
  id,
}: BigButtonProps) {
  return (
    <button
      id={id}
      type="button"
      onClick={onClick}
      disabled={disabled}
      className={`db-big-button db-big-button--${variant} ${className}`}
      data-testid="big-button"
    >
      {icon && <span className="db-big-button__icon">{icon}</span>}
      {children && <span className="db-big-button__text">{children}</span>}
    </button>
  );
}
