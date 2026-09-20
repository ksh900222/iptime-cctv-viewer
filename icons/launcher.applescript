on run
	set appPath to POSIX path of (path to me)
	set launcher to appPath & "Contents/Resources/launcher.sh"
	try
		with timeout of 2592000 seconds
			do shell script "/bin/bash " & quoted form of launcher
		end timeout
	on error errMsg number errNum
		if errNum is not 0 and errNum is not -128 then
			display dialog errMsg buttons {"확인"} default button 1 with title "CCTV 뷰어" with icon stop
		end if
	end try
end run
