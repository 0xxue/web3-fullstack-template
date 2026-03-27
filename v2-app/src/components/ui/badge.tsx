import * as React from "react"
import { cn } from "@/lib/utils"

export interface BadgeProps extends React.HTMLAttributes<HTMLDivElement> {
    variant?: "default" | "success" | "warning" | "destructive" | "outline" | "secondary"
}

function Badge({ className, variant = "default", ...props }: BadgeProps) {
    return (
        <div
            className={cn(
                "inline-flex items-center whitespace-nowrap rounded-md px-2 py-1 text-xs font-semibold transition-colors focus:outline-none focus:ring-2 focus:ring-ring focus:ring-offset-2",
                {
                    "bg-blue-50 text-blue-600 dark:bg-blue-500/10 dark:text-blue-400": variant === "default",
                    "bg-emerald-50 text-emerald-600 dark:bg-emerald-500/10 dark:text-emerald-400": variant === "success",
                    "bg-amber-50 text-amber-600 dark:bg-amber-500/10 dark:text-amber-400 border border-amber-100 dark:border-amber-500/20": variant === "warning",
                    "bg-red-50 text-red-600 dark:bg-red-500/10 dark:text-red-400": variant === "destructive",
                    "bg-gray-100 text-gray-500 dark:bg-[#2a2d35] dark:text-gray-400": variant === "secondary",
                    "border border-input bg-background text-foreground": variant === "outline",
                },
                className
            )}
            {...props}
        />
    )
}

export { Badge }
