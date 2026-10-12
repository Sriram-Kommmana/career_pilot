import type { HTMLAttributes } from "react";
import { cva, type VariantProps } from "class-variance-authority";
import { cn } from "@/lib/cn";
import { AlertTriangle, Info, CheckCircle2, XCircle } from "lucide-react";

const alertVariants = cva("flex items-start gap-3 rounded-lg border p-4 text-sm", {
  variants: {
    variant: {
      info: "border-blue-200 bg-blue-50 text-blue-900",
      warning: "border-amber-200 bg-amber-50 text-amber-900",
      success: "border-emerald-200 bg-emerald-50 text-emerald-900",
      danger: "border-red-200 bg-red-50 text-red-900",
    },
  },
  defaultVariants: { variant: "info" },
});

const icons = {
  info: Info,
  warning: AlertTriangle,
  success: CheckCircle2,
  danger: XCircle,
};

type AlertVariant = keyof typeof icons;

export interface AlertProps
  extends HTMLAttributes<HTMLDivElement>,
    Omit<VariantProps<typeof alertVariants>, "variant"> {
  variant?: AlertVariant;
  title?: string;
}

export function Alert({ className, variant = "info", title, children, ...props }: AlertProps) {
  const Icon = icons[variant];
  return (
    <div className={cn(alertVariants({ variant, className }))} {...props}>
      <Icon className="mt-0.5 h-4 w-4 flex-shrink-0" aria-hidden="true" />
      <div className="flex-1">
        {title && <p className="mb-0.5 font-semibold">{title}</p>}
        <div>{children}</div>
      </div>
    </div>
  );
}
