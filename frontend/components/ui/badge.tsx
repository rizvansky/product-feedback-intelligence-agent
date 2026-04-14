import * as React from "react";

import { cn } from "@/lib/utils";

export function Badge({
  className,
  tone = "neutral",
  ...props
}: React.HTMLAttributes<HTMLSpanElement> & {
  tone?: "neutral" | "success" | "danger";
}) {
  return (
    <span
      className={cn(
        "inline-flex rounded-full px-4 py-1.5 text-sm font-semibold",
        tone === "success" && "bg-pine/10 text-pine",
        tone === "danger" && "bg-ember/10 text-ember",
        tone === "neutral" && "bg-mist text-ink",
        className
      )}
      {...props}
    />
  );
}
