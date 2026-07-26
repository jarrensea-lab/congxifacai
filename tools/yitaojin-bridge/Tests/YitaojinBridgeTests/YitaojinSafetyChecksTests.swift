// The build-tool plugin attached to this formal SwiftPM test target runs the
// existing 49 SelfTests before this target can build. Keeping this source
// framework-free lets the safety gate work with Command Line Tools alone.
enum YitaojinSafetyChecksTests {}
