import SwiftUI

/// Read-only view over `MemoryStore` — lets you see exactly what the app has captured
/// from past conversations, which is the whole point of this prototype: confirming that
/// locally accumulated memory can actually ground a spoken answer later.
struct MemoryView: View {
    @Environment(\.dismiss) private var dismiss
    let memoryStore: MemoryStore
    @State private var entries: [MemoryEntry] = []

    var body: some View {
        NavigationStack {
            Group {
                if entries.isEmpty {
                    EmptyMemoryView()
                } else {
                    List(entries) { entry in
                        VStack(alignment: .leading, spacing: 4) {
                            Text(entry.text)
                            Text(entry.timestamp, style: .relative)
                                .font(.caption)
                                .foregroundStyle(.secondary)
                        }
                    }
                }
            }
            .navigationTitle("本地记忆")
            .toolbar {
                ToolbarItem(placement: .cancellationAction) {
                    Button("关闭") { dismiss() }
                }
            }
            .onAppear {
                entries = memoryStore.all()
            }
        }
    }
}

private struct EmptyMemoryView: View {
    var body: some View {
        VStack(spacing: 8) {
            Image(systemName: "brain")
                .font(.system(size: 36))
                .foregroundStyle(.secondary)
            Text("还没有记忆")
                .foregroundStyle(.secondary)
        }
    }
}

#Preview {
    MemoryView(memoryStore: MemoryStore())
}
