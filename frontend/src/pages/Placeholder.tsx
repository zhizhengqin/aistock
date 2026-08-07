export default function Placeholder({ title }: { title: string }) {
  return (
    <div className="flex flex-col items-center justify-center h-full text-gray-400">
      <span className="text-6xl mb-4">🚧</span>
      <p className="text-xl font-semibold">{title}</p>
      <p className="text-sm mt-2">即将开放，敬请期待</p>
    </div>
  )
}
