import SwiftUI

struct LoginView: View {
    @EnvironmentObject private var appState: AppState
    @State private var email = ""
    @State private var password = ""
    @FocusState private var focusedField: Field?

    private enum Field { case email, password }

    var body: some View {
        NavigationStack {
            ScrollView {
                VStack(spacing: 24) {
                    Spacer(minLength: 28)
                    Image("BrandMonogram")
                        .resizable()
                        .scaledToFit()
                        .frame(width: 112, height: 112)
                        .accessibilityHidden(true)
                    VStack(spacing: 6) {
                        Text("ClearCode Reading")
                            .font(.largeTitle.bold())
                            .foregroundStyle(Brand.ink)
                        Text("Unlock Reading. Unlock Everything.")
                            .foregroundStyle(Brand.forest)
                    }

                    VStack(spacing: 14) {
                        TextField("Email address", text: $email)
                            .textContentType(.username)
                            .keyboardType(.emailAddress)
                            .textInputAutocapitalization(.never)
                            .autocorrectionDisabled()
                            .submitLabel(.next)
                            .focused($focusedField, equals: .email)
                            .onSubmit { focusedField = .password }
                            .accessibilityIdentifier("login.email")
                        SecureField("Password", text: $password)
                            .textContentType(.password)
                            .submitLabel(.go)
                            .focused($focusedField, equals: .password)
                            .onSubmit { signIn() }
                            .accessibilityIdentifier("login.password")
                    }
                    .textFieldStyle(.roundedBorder)

                    Button(action: signIn) {
                        HStack {
                            if appState.isBusy { ProgressView().tint(.white) }
                            Text(appState.isBusy ? "Signing in…" : "Sign In")
                                .fontWeight(.semibold)
                        }
                        .frame(maxWidth: .infinity)
                        .padding(.vertical, 12)
                    }
                    .buttonStyle(.borderedProminent)
                    .tint(Brand.deepTeal)
                    .disabled(appState.isBusy || email.isEmpty || password.isEmpty)
                    .accessibilityIdentifier("login.submit")

                    Text("Your access is determined by your ClearCode role, center membership, and family consent settings.")
                        .font(.footnote)
                        .foregroundStyle(.secondary)
                        .multilineTextAlignment(.center)
                }
                .padding(24)
                .frame(maxWidth: 520)
                .frame(maxWidth: .infinity)
            }
            .background(Brand.linen.ignoresSafeArea())
        }
    }

    private func signIn() {
        focusedField = nil
        Task { await appState.signIn(email: email, password: password) }
    }
}
