import * as React from "react"
import { ChevronDown, Check } from "lucide-react"
import { cn } from "@/lib/utils"

interface SelectOption {
    value: string
    label: string
    sublabel?: string
}

interface SelectProps {
    value: string
    onChange: (value: string) => void
    options: SelectOption[]
    className?: string
    placeholder?: string
    disabled?: boolean
}

export function Select({ value, onChange, options, className, placeholder, disabled }: SelectProps) {
    const [isOpen, setIsOpen] = React.useState(false)
    const ref = React.useRef<HTMLDivElement>(null)

    React.useEffect(() => {
        const handler = (e: MouseEvent) => {
            if (ref.current && !ref.current.contains(e.target as Node)) {
                setIsOpen(false)
            }
        }
        document.addEventListener("mousedown", handler)
        return () => document.removeEventListener("mousedown", handler)
    }, [])

    const selectedLabel = options.find((o) => o.value === value)?.label || placeholder || value

    return (
        <div ref={ref} className={cn("relative", className)}>
            <button
                type="button"
                onClick={() => !disabled && setIsOpen(!isOpen)}
                disabled={disabled}
                className={cn(
                    "flex h-11 w-full items-center justify-between rounded-xl border bg-white px-4 py-2 text-sm shadow-sm transition-all duration-200 cursor-pointer",
                    "border-gray-200 text-zinc-900 hover:border-gray-300",
                    "dark:border-[#2a2d35] dark:bg-[#1c1f26] dark:text-gray-200 dark:hover:border-[#3a3e47]",
                    "focus:outline-none focus:ring-2 focus:ring-blue-500/20 focus:border-blue-400",
                    isOpen && "ring-2 ring-blue-500/20 border-blue-400 dark:border-blue-500/50",
                    disabled && "opacity-50 cursor-not-allowed"
                )}
            >
                <span className="truncate">{selectedLabel}</span>
                <ChevronDown className={cn(
                    "w-4 h-4 text-gray-400 transition-transform duration-200 shrink-0 ml-2",
                    isOpen && "rotate-180"
                )} />
            </button>

            {isOpen && (
                <div className="absolute top-full left-0 right-0 mt-1.5 bg-white dark:bg-[#181a20] rounded-xl border border-gray-100 dark:border-[#2a2d35] shadow-xl z-50 py-1.5 animate-slide-down overflow-hidden">
                    {options.map((option) => (
                        <button
                            key={option.value}
                            type="button"
                            onClick={() => { onChange(option.value); setIsOpen(false) }}
                            className={cn(
                                "w-full px-4 py-2 text-sm text-left transition-colors flex items-center justify-between gap-2",
                                value === option.value
                                    ? "bg-blue-50 dark:bg-blue-500/10 text-blue-600 dark:text-blue-400 font-medium"
                                    : "text-zinc-700 dark:text-gray-300 hover:bg-gray-50 dark:hover:bg-[#22252e]"
                            )}
                        >
                            <span className="flex flex-col min-w-0">
                                <span className="truncate">{option.label}</span>
                                {option.sublabel && (
                                    <span className={cn(
                                        "text-[11px] font-normal mt-0.5",
                                        value === option.value ? "text-blue-400/80" : "text-gray-400 dark:text-gray-500"
                                    )}>{option.sublabel}</span>
                                )}
                            </span>
                            {value === option.value && <Check className="w-4 h-4 shrink-0" />}
                        </button>
                    ))}
                </div>
            )}
        </div>
    )
}
