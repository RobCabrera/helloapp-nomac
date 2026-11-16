import SwiftUI

struct ContentView: View {
    @State private var taps = 0

    var body: some View {
        VStack(spacing: 16) {
            Text("Built without a Mac")
                .font(.title)
                .bold()
            Text("Taps: \(taps)")
            Button("Tap") { taps += 1 }
                .buttonStyle(.borderedProminent)
        }
        .padding()
    }
}
