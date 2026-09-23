import Capacitor
import Foundation
import Security

@objc(PinnedCertificatePlugin)
final class PinnedCertificatePlugin: CAPInstancePlugin, CAPBridgedPlugin {
    let identifier = "PinnedCertificatePlugin"
    let jsName = "PinnedCertificate"
    let pluginMethods: [CAPPluginMethod] = []

    private let pinnedHost = "39.105.10.183"
    private lazy var pinnedCertificateData: Data? = {
        guard let url = Bundle.main.url(
            forResource: "server_39_105_10_183",
            withExtension: "cer"
        ) else {
            return nil
        }
        return try? Data(contentsOf: url)
    }()

    override func handleWKWebViewURLAuthenticationChallenge(
        _ challenge: URLAuthenticationChallenge,
        completionHandler: @escaping (URLSession.AuthChallengeDisposition, URLCredential?) -> Void
    ) -> Bool {
        guard challenge.protectionSpace.authenticationMethod == NSURLAuthenticationMethodServerTrust else {
            return false
        }

        guard challenge.protectionSpace.host == pinnedHost,
              let serverTrust = challenge.protectionSpace.serverTrust,
              let expectedData = pinnedCertificateData,
              let certificateChain = SecTrustCopyCertificateChain(serverTrust) as? [SecCertificate],
              let serverCertificate = certificateChain.first,
              SecCertificateCopyData(serverCertificate) as Data == expectedData,
              let pinnedCertificate = SecCertificateCreateWithData(nil, expectedData as CFData) else {
            completionHandler(.cancelAuthenticationChallenge, nil)
            return true
        }

        let sslPolicy = SecPolicyCreateSSL(true, pinnedHost as CFString)
        SecTrustSetPolicies(serverTrust, sslPolicy)
        SecTrustSetAnchorCertificates(serverTrust, [pinnedCertificate] as CFArray)
        SecTrustSetAnchorCertificatesOnly(serverTrust, false)

        var trustError: CFError?
        guard SecTrustEvaluateWithError(serverTrust, &trustError) else {
            completionHandler(.cancelAuthenticationChallenge, nil)
            return true
        }

        completionHandler(.useCredential, URLCredential(trust: serverTrust))
        return true
    }
}
