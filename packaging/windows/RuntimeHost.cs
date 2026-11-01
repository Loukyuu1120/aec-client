using System;
using System.IO;
using System.Reflection;
using System.Runtime.InteropServices;

[assembly: AssemblyTitle("AEC Studio")]
[assembly: AssemblyProduct("AEC Studio")]
[assembly: AssemblyDescription("AEC Studio audio processing")]
[assembly: AssemblyVersion("0.11.0.0")]
[assembly: AssemblyFileVersion("0.11.0.0")]

internal static class RuntimeHost
{
    [DllImport("python312.dll", CallingConvention = CallingConvention.Cdecl)]
    private static extern int Py_Main(int count, IntPtr arguments);

    [STAThread]
    private static int Main(string[] args)
    {
        IntPtr[] strings = new IntPtr[args.Length + 1];
        IntPtr arguments = IntPtr.Zero;
        try
        {
            strings[0] = Marshal.StringToHGlobalUni(Assembly.GetExecutingAssembly().Location);
            for (int index = 0; index < args.Length; index++)
                strings[index + 1] = Marshal.StringToHGlobalUni(args[index]);
            arguments = Marshal.AllocHGlobal((strings.Length + 1) * IntPtr.Size);
            Marshal.Copy(strings, 0, arguments, strings.Length);
            Marshal.WriteIntPtr(arguments, strings.Length * IntPtr.Size, IntPtr.Zero);
            return Py_Main(strings.Length, arguments);
        }
        catch (Exception error)
        {
            try
            {
                string directory = Environment.GetEnvironmentVariable("AEC_STUDIO_DATA_DIR");
                if (String.IsNullOrEmpty(directory))
                    directory = Path.Combine(Environment.GetFolderPath(Environment.SpecialFolder.LocalApplicationData), "AECStudio", "data");
                Directory.CreateDirectory(directory);
                File.AppendAllText(Path.Combine(directory, "host.log"), error.ToString() + Environment.NewLine);
            }
            catch { }
            return 1;
        }
        finally
        {
            if (arguments != IntPtr.Zero) Marshal.FreeHGlobal(arguments);
            foreach (IntPtr value in strings)
                if (value != IntPtr.Zero) Marshal.FreeHGlobal(value);
        }
    }
}
