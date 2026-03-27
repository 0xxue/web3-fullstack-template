import * as React from "react"
import { X } from "lucide-react"
import { cn } from "@/lib/utils"

interface ModalProps {
    isOpen: boolean
    onClose: () => void
    title?: React.ReactNode
    children: React.ReactNode
    className?: string
}

export function Modal({ isOpen, onClose, title, children, className }: ModalProps) {
    if (!isOpen) return null

    return (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/50 p-4 sm:p-0 backdrop-blur-sm animate-in fade-in duration-200">
            <div
                className={cn(
                    "relative w-full max-w-lg rounded-2xl bg-white p-6 shadow-xl dark:bg-[#181a20] dark:border dark:border-[#2a2d35] animate-in zoom-in-95 duration-200",
                    className
                )}
            >
                <div className="flex items-center justify-between mb-5">
                    {title && <h2 className="text-xl font-bold text-zinc-900 dark:text-white">{title}</h2>}
                    <button
                        onClick={onClose}
                        className="rounded-full p-1.5 text-gray-400 hover:bg-gray-100 hover:text-gray-900 dark:hover:bg-[#2a2d35] dark:hover:text-white transition-colors"
                    >
                        <X className="w-5 h-5" />
                    </button>
                </div>
                <div>{children}</div>
            </div>
        </div>
    )
}
